mod jsonrpc;
mod lsp;
mod qls;
use clap::Parser;
use q_lint_rs::{RULES, lint};
use std::{
    collections::BTreeSet,
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
    process::ExitCode,
};

#[derive(Parser)]
#[command(about = "Lint q source without executing it")]
struct Args {
    paths: Vec<String>,
    #[arg(long,default_value="text",value_parser=["text","json"])]
    format: String,
    #[arg(long,default_value="general",value_parser=["general","uqf"])]
    profile: String,
    #[arg(long, default_value = "<stdin>")]
    stdin_filename: String,
    #[arg(long)]
    config: Option<PathBuf>,
    #[arg(long)]
    exclude: Vec<String>,
    #[arg(long)]
    rules: bool,
    #[arg(long)]
    explain: Option<String>,
    #[arg(long,default_value="builtin",value_parser=["builtin","qls","all"])]
    backend: String,
    #[arg(long, default_value = "qls")]
    qls_executable: String,
    #[arg(long, default_value = "30")]
    qls_timeout: f64,
    /// Run as a language server on stdin/stdout instead of linting paths.
    #[arg(long, conflicts_with_all = ["paths", "rules", "explain"])]
    lsp: bool,
}
fn config(explicit: Option<&Path>) -> Result<(PathBuf, Vec<glob::Pattern>), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    let paths: Vec<_> = match explicit {
        Some(p) => vec![p.to_path_buf()],
        None => cwd.ancestors().map(|p| p.join("pyproject.toml")).collect(),
    };
    for path in paths {
        if explicit.is_none() && !path.is_file() {
            continue;
        }
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let data: toml::Value =
            toml::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        if data.get("tool").is_some_and(|t| !t.is_table()) {
            return Err("tool must be a table".into());
        }
        if let Some(settings) = data.get("tool").and_then(|t| t.get("q-lint")) {
            let table = settings.as_table().ok_or("[tool.q-lint] must be a table")?;
            if table.keys().any(|k| k != "exclude") {
                return Err("[tool.q-lint] supports only exclude".into());
            }
            let empty = vec![];
            let patterns = match table.get("exclude") {
                None => &empty,
                Some(v) => v.as_array().ok_or("exclude must be an array")?,
            };
            let patterns = patterns
                .iter()
                .map(|v| {
                    let s = v
                        .as_str()
                        .filter(|s| !s.trim().is_empty())
                        .ok_or("exclude must contain nonempty strings")?;
                    glob::Pattern::new(s.trim_end_matches('/')).map_err(|e| e.to_string())
                })
                .collect::<Result<Vec<_>, _>>()?;
            return Ok((
                fs::canonicalize(path)
                    .map_err(|e| e.to_string())?
                    .parent()
                    .unwrap()
                    .to_path_buf(),
                patterns,
            ));
        }
        if explicit.is_some() {
            return Err("missing [tool.q-lint]".into());
        }
    }
    Ok((cwd, vec![]))
}
fn absolute(path: &Path) -> PathBuf {
    if let Ok(p) = fs::canonicalize(path) {
        return p;
    }
    let mut result = PathBuf::new();
    let p = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().unwrap().join(path)
    };
    for c in p.components() {
        match c {
            std::path::Component::ParentDir => {
                result.pop();
            }
            std::path::Component::CurDir => {}
            _ => result.push(c),
        }
    }
    result
}
fn excluded(path: &Path, root: &Path, patterns: &[glob::Pattern]) -> bool {
    let full = absolute(path);
    let Ok(relative) = full.strip_prefix(root) else {
        return false;
    };
    relative
        .ancestors()
        .filter(|p| !p.as_os_str().is_empty())
        .any(|p| patterns.iter().any(|g| g.matches(&p.to_string_lossy())))
}
fn run(args: Args) -> Result<u8, String> {
    if args.lsp {
        // Locked once for the life of the process: the server owns both
        // streams, and re-locking per message would be pure overhead.
        let stdin = io::stdin();
        let stdout = io::stdout();
        return lsp::serve(&mut stdin.lock(), &mut stdout.lock(), args.profile == "uqf");
    }
    if args.rules || args.explain.is_some() {
        let entries: Vec<_> = RULES
            .iter()
            .filter(|r| args.explain.as_ref().is_none_or(|code| r.code == *code))
            .collect();
        if entries.is_empty() {
            return Err(format!(
                "Unknown diagnostic code: {}",
                args.explain.unwrap()
            ));
        }
        if args.format == "json" {
            println!("{}", serde_json::to_string(&entries).unwrap());
        } else {
            for r in entries {
                println!(
                    "{} [{}] {}: {} ({})",
                    r.code, r.category, r.name, r.summary, r.scope
                );
            }
        }
        return Ok(0);
    }
    if !args.qls_timeout.is_finite() || args.qls_timeout <= 0.0 || args.qls_timeout > 1e9 {
        return Err("--qls-timeout must be positive, finite, and at most 1e9 seconds".into());
    }
    let (root, mut patterns) = config(args.config.as_deref())?;
    for p in &args.exclude {
        patterns.push(glob::Pattern::new(p.trim_end_matches('/')).map_err(|e| e.to_string())?);
    }
    let paths = if args.paths.is_empty() {
        vec![".".into()]
    } else {
        args.paths
    };
    let mut sources = vec![];
    if paths.iter().any(|p| p == "-") {
        if paths.len() != 1 {
            return Err("Stdin (-) must be the only input".into());
        }
        if args.stdin_filename == "<stdin>"
            || !excluded(Path::new(&args.stdin_filename), &root, &patterns)
        {
            let mut source = String::new();
            io::stdin()
                .read_to_string(&mut source)
                .map_err(|e| e.to_string())?;
            sources.push((args.stdin_filename, source));
        }
    } else {
        let mut files = BTreeSet::new();
        let mut skipped = false;
        for name in paths {
            let path = Path::new(&name);
            if !path.exists() {
                return Err(format!("Path does not exist: {name}"));
            }
            if excluded(path, &root, &patterns) {
                skipped = true;
                continue;
            }
            if path.is_file() {
                files.insert(absolute(path));
                continue;
            }
            if !path.is_dir() {
                return Err(format!("Not a file/directory: {name}"));
            }
            let mut walk = walkdir::WalkDir::new(path).into_iter();
            while let Some(entry) = walk.next() {
                let entry = entry.map_err(|e| e.to_string())?;
                let p = entry.path();
                if excluded(p, &root, &patterns) {
                    skipped = true;
                    if entry.file_type().is_dir() {
                        walk.skip_current_dir();
                    }
                    continue;
                }
                if entry.depth() > 0
                    && entry.file_type().is_dir()
                    && [
                        ".git",
                        ".venv",
                        "node_modules",
                        "__pycache__",
                        "build",
                        "dist",
                        "target",
                    ]
                    .iter()
                    .any(|n| entry.file_name() == *n)
                {
                    walk.skip_current_dir();
                    continue;
                }
                if p.is_file() && p.extension().is_some_and(|e| e == "q") {
                    files.insert(absolute(p));
                }
            }
        }
        if files.is_empty() && !skipped {
            return Err("No .q files found".into());
        }
        for path in files {
            let source =
                fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
            sources.push((path.to_string_lossy().into_owned(), source));
        }
    }
    let mut findings = vec![];
    if args.backend != "qls" {
        for (path, source) in &sources {
            findings.extend(lint(source, path, args.profile == "uqf"));
        }
    }
    if args.backend != "builtin" {
        findings.extend(qls::lint(
            sources.clone(),
            &args.qls_executable,
            args.qls_timeout,
        )?);
    }
    findings.sort_by(|a, b| {
        (&a.path, a.line, a.column, &a.source, &a.rule)
            .cmp(&(&b.path, b.line, b.column, &b.source, &b.rule))
    });
    if args.format == "json" {
        println!("{}", serde_json::to_string(&findings).unwrap());
    } else {
        for f in &findings {
            let col = f.column.map_or(String::new(), |c| format!(":{c}"));
            println!(
                "{}:{}{}: {}: {} [{}/{}] {}\n    {}",
                f.path, f.line, col, f.severity, f.code, f.category, f.rule, f.detail, f.why
            );
        }
        println!(
            "qlinter: {} file(s), {} finding(s)",
            sources.len(),
            findings.len()
        );
    }
    Ok(u8::from(findings.iter().any(|f| {
        matches!(f.severity.as_str(), "error" | "warning")
    })))
}
fn main() -> ExitCode {
    match run(Args::parse()) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("qlinter: {error}");
            ExitCode::from(2)
        }
    }
}
