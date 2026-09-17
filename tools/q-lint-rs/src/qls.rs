//! Bounded batch LSP client. No source execution and no editor dependency.
use crate::jsonrpc::{receive, send};
use q_lint_rs::Finding;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::Path,
    process::{Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

fn next(reader: &mut impl BufRead, writer: &mut impl Write) -> Result<Value, String> {
    loop {
        let message = receive(reader)?.ok_or("qls closed stdout before analysis completed")?;
        if message.get("id").is_none() || message.get("method").is_none() {
            return Ok(message);
        }
        let result = match message["method"].as_str().unwrap_or("") {
            "workspace/configuration" => Value::Array(
                message["params"]["items"]
                    .as_array()
                    .ok_or("Invalid configuration request")?
                    .iter()
                    .map(
                        |item| match item["section"].as_str().unwrap_or("q-lang-server") {
                            "q-lang-server" => {
                                json!({"sourceFiles":{"includeGlob":[],"ignoreGlob":[]}})
                            }
                            "q-lang-server.sourceFiles" => {
                                json!({"includeGlob":[],"ignoreGlob":[]})
                            }
                            "q-lang-server.sourceFiles.includeGlob"
                            | "q-lang-server.sourceFiles.ignoreGlob" => json!([]),
                            _ => Value::Null,
                        },
                    )
                    .collect(),
            ),
            "workspace/workspaceFolders" => json!([]),
            "client/registerCapability" | "window/workDoneProgress/create" => Value::Null,
            _ => {
                send(
                    writer,
                    json!({"id":message["id"],"error":{"code":-32601,"message":"Unsupported client method"}}),
                )?;
                continue;
            }
        };
        send(writer, json!({"id":message["id"],"result":result}))?;
    }
}
fn uri(path: &Path) -> String {
    let mut result = "file://".to_string();
    for b in path.to_string_lossy().bytes() {
        if b.is_ascii_alphanumeric() || b"/-._~".contains(&b) {
            result.push(b as char);
        } else {
            result.push_str(&format!("%{b:02X}"));
        }
    }
    result
}
fn findings(path: &str, values: &Value) -> Result<Vec<Finding>, String> {
    let mut out = vec![];
    for d in values
        .as_array()
        .ok_or("qls diagnostics must be an array")?
    {
        let pos = |which: &str, key: &str| -> Result<usize, String> {
            d["range"][which][key]
                .as_u64()
                .and_then(|v| usize::try_from(v).ok())
                .and_then(|v| v.checked_add(1))
                .ok_or("Malformed qls diagnostic range".into())
        };
        let message = d["message"]
            .as_str()
            .ok_or("Malformed qls diagnostic message")?;
        let mut f = Finding::new(path, pos("start", "line")?, "QLS001", message.into());
        f.column = Some(pos("start", "character")?);
        f.end_line = Some(pos("end", "line")?);
        f.end_column = Some(pos("end", "character")?);
        let code = d
            .get("code")
            .map(|c| {
                c.as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| c.to_string())
            })
            .unwrap_or("diagnostic".into());
        f.rule = format!("qls/{code}");
        f.source = "qls".into();
        f.why.clear();
        f.severity = match d["severity"].as_u64().unwrap_or(1) {
            2 => "warning",
            3 => "information",
            4 => "hint",
            _ => "error",
        }
        .into();
        out.push(f);
    }
    Ok(out)
}
fn analyze(
    reader: &mut impl BufRead,
    writer: &mut impl Write,
    root: &str,
    sources: &[(String, String)],
) -> Result<Vec<Finding>, String> {
    send(
        writer,
        json!({"id":1,"method":"initialize","params":{"processId":null,"rootUri":root,"workspaceFolders":[{"uri":root,"name":"q-lint"}],"capabilities":{"workspace":{"configuration":true}}}}),
    )?;
    loop {
        let m = next(reader, writer)?;
        if m["id"] == 1 {
            if m.get("error").is_some() || m.get("result").is_none() {
                return Err(format!("qls initialization failed: {m}"));
            }
            break;
        }
    }
    send(writer, json!({"method":"initialized","params":{}}))?;
    let mut opened = HashMap::new();
    let mut diagnostics = HashMap::new();
    for version in 1..=if sources.len() > 1 { 2 } else { 1 } {
        for (index, (path, source)) in sources.iter().enumerate() {
            let uri = format!("{root}/document_{index}.q");
            opened.insert(uri.clone(), path);
            let message = if version == 1 {
                json!({"method":"textDocument/didOpen","params":{"textDocument":{"uri":uri,"languageId":"q","version":version,"text":source}}})
            } else {
                json!({"method":"textDocument/didChange","params":{"textDocument":{"uri":uri,"version":version},"contentChanges":[{"text":source}]}})
            };
            send(writer, message)?;
            loop {
                let m = next(reader, writer)?;
                if m["method"] == "textDocument/publishDiagnostics" {
                    let target = m["params"]["uri"]
                        .as_str()
                        .ok_or("Malformed diagnostics URI")?;
                    if let Some(path) = opened.get(target) {
                        diagnostics.insert(
                            target.to_string(),
                            findings(path, &m["params"]["diagnostics"])?,
                        );
                    }
                    if target == uri {
                        break;
                    }
                }
            }
        }
    }
    Ok(diagnostics.into_values().flatten().collect())
}
pub fn lint(
    sources: Vec<(String, String)>,
    executable: &str,
    timeout: f64,
) -> Result<Vec<Finding>, String> {
    if sources.is_empty() {
        return Ok(vec![]);
    }
    let executable = if Path::new(executable).is_file() {
        std::fs::canonicalize(executable)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .into_owned()
    } else {
        executable.into()
    };
    let root = tempfile::tempdir().map_err(|e| e.to_string())?;
    let root_uri = uri(root.path());
    let mut log = tempfile::tempfile().map_err(|e| e.to_string())?;
    let mut child = Command::new(executable)
        .current_dir(root.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(log.try_clone().map_err(|e| e.to_string())?)
        .spawn()
        .map_err(|e| format!("Cannot start qls: {e}"))?;
    let mut reader = BufReader::new(child.stdout.take().unwrap());
    let mut writer = child.stdin.take().unwrap();
    let (tx, rx) = mpsc::channel();
    let worker = thread::spawn(move || {
        let result = analyze(&mut reader, &mut writer, &root_uri, &sources);
        let _ = tx.send(result);
        if send(
            &mut writer,
            json!({"id":2,"method":"shutdown","params":null}),
        )
        .is_ok()
        {
            while let Ok(m) = next(&mut reader, &mut writer) {
                if m["id"] == 2 {
                    break;
                }
            }
            let _ = send(&mut writer, json!({"method":"exit"}));
        }
    });
    let result = rx
        .recv_timeout(Duration::from_secs_f64(timeout))
        .unwrap_or_else(|_| Err(format!("qls did not complete analysis within {timeout}s")));
    let deadline = Instant::now() + Duration::from_secs(1);
    while Instant::now() < deadline {
        if worker.is_finished() && child.try_wait().ok().flatten().is_some() {
            break;
        }
        thread::sleep(Duration::from_millis(5));
    }
    let _ = child.kill();
    let _ = child.wait();
    if worker.is_finished() {
        let _ = worker.join();
    }
    result.map_err(|error| {
        let _ = log.seek(SeekFrom::Start(0));
        let mut detail = String::new();
        let _ = log.take(4096).read_to_string(&mut detail);
        format!(
            "{error}{}",
            if detail.trim().is_empty() {
                String::new()
            } else {
                format!("; stderr: {}", detail.trim())
            }
        )
    })
}
