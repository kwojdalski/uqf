use crate::{Finding, boundary, line_at, matching};
use std::collections::{HashMap, HashSet};

struct Scope {
    start: usize,
    end: usize,
    body: usize,
    parent: Option<usize>,
    namespace: String,
    params: HashSet<String>,
    locals: HashSet<String>,
    direct: String,
}
fn qualify(name: &str, ns: &str) -> String {
    if name.starts_with('.') {
        name.into()
    } else {
        format!("{ns}.{name}")
    }
}
fn scope_at(scopes: &[Scope], at: usize) -> Option<usize> {
    scopes.iter().rposition(|s| s.start < at && at < s.end)
}
fn shape(s: &str) -> Option<usize> {
    let s = s.trim();
    if re!(r"\n\S").is_match(s) {
        return None;
    }
    if s.starts_with('(') && matching(s, 0, b'(', b')') == Some(s.len()) {
        return shape(&s[1..s.len() - 1]);
    }
    if let Some(rest) = s.strip_prefix("enlist ") {
        return shape(rest).map(|_| 1);
    }
    if re!(r"^(?:`[A-Za-z][A-Za-z0-9_.]*)+$").is_match(s) {
        return Some(s.bytes().filter(|&b| b == b'`').count());
    }
    if re!(r"^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[bhijef]?(?:\s+-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[bhijef]?)*$").is_match(s) {return Some(s.split_whitespace().count());}
    None
}
pub fn check(path: &str, code: &str) -> Vec<Finding> {
    let mut out = vec![];
    for m in re!(r"((?:`[A-Za-z][A-Za-z0-9_.]*)+)\s*!").captures_iter(code) {
        let start = m.get(0).unwrap();
        let mut end = start.end();
        let mut depth = 0;
        for b in code[end..].bytes() {
            if b")]}".contains(&b) {
                if depth == 0 {
                    break;
                }
                depth -= 1;
            } else if b"([{ ".contains(&b) && b != b' ' {
                depth += 1;
            } else if b == b';' && depth == 0 {
                break;
            }
            end += 1;
        }
        if let (Some(keys), Some(values)) = (shape(&m[1]), shape(&code[start.end()..end]))
            && keys != values
        {
            out.push(Finding::new(
                path,
                line_at(code, start.start()),
                "QT001",
                format!("Literal dictionary has {keys} keys and {values} values"),
            ));
        }
    }
    if re!(r"(?m)^\\l\b").is_match(code)
        || re!(r"\b(?:set|value|eval|system)\b")
            .find_iter(code)
            .any(|m| boundary(code, m.start()))
    {
        return out;
    }
    let assignment = re!(r"(\.?[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)\s*:(:)?");
    let mut scopes: Vec<Scope> = vec![];
    let mut stack: Vec<usize> = vec![];
    let mut namespace = String::new();
    let mut namespaces = vec![];
    let mut offset = 0;
    for line in code.split_inclusive('\n') {
        if stack.is_empty()
            && let Some(m) = re!(r"^\\d\s+(\.[\w.]*)\s*$").captures(line)
        {
            namespace = m[1].trim_end_matches('.').into();
        }
        namespaces.push((offset, namespace.clone()));
        if line.starts_with('\\') {
            offset += line.len();
            continue;
        }
        for (i, b) in line.bytes().enumerate() {
            let at = offset + i;
            if b == b'{' {
                let sig = re!(r"^\s*\[([^\]{}]*)\]").captures(&code[at + 1..]);
                let (params, body) = match sig {
                    Some(m) => (
                        m[1].split(';')
                            .map(str::trim)
                            .filter(|p| !p.is_empty())
                            .map(str::to_string)
                            .collect(),
                        at + 1 + m.get(0).unwrap().end(),
                    ),
                    None => (
                        ["x", "y", "z"].into_iter().map(str::to_string).collect(),
                        at + 1,
                    ),
                };
                scopes.push(Scope {
                    start: at,
                    end: code.len(),
                    body,
                    parent: stack.last().copied(),
                    namespace: namespace.clone(),
                    params,
                    locals: HashSet::new(),
                    direct: String::new(),
                });
                stack.push(scopes.len() - 1);
            } else if b == b'}'
                && let Some(s) = stack.pop()
            {
                scopes[s].end = at;
            }
        }
        offset += line.len();
    }
    for i in 0..scopes.len() {
        let scope = &scopes[i];
        let mut direct = code.as_bytes()[scope.body..scope.end].to_vec();
        for child in &scopes {
            if child.parent == Some(i) {
                direct[child.start - scope.body..child.end + 1 - scope.body].fill(b' ');
            }
        }
        let direct = String::from_utf8(direct).unwrap();
        let mut locals = scope.params.clone();
        for a in assignment.captures_iter(&direct) {
            if boundary(&direct, a.get(0).unwrap().start())
                && a.get(2).is_none()
                && !a[1].contains('.')
            {
                locals.insert(a[1].into());
            }
        }
        scopes[i].direct = direct;
        scopes[i].locals = locals;
    }
    let ns_at = |at| {
        namespaces[namespaces
            .partition_point(|(p, _)| *p <= at)
            .saturating_sub(1)]
        .1
        .as_str()
    };
    let mut globals: HashMap<String, Vec<usize>> = HashMap::new();
    for a in assignment.captures_iter(code) {
        let m = a.get(0).unwrap();
        if !boundary(code, m.start()) {
            continue;
        }
        if scope_at(&scopes, m.start()).is_none() || a.get(2).is_some() || a[1].contains('.') {
            globals
                .entry(qualify(&a[1], ns_at(m.start())))
                .or_default()
                .push(m.end());
        }
    }
    let mut numeric = HashSet::new();
    for (name, assignments) in &globals {
        if assignments.len() != 1 {
            continue;
        }
        let start = code.len() - code[assignments[0]..].trim_start().len();
        if let Some(scope) = scopes
            .iter()
            .find(|s| s.start == start && s.params.len() == 1)
            && let Some(m)=re!(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*[+*%\-]\s*-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[bhijef]?\s*;?\s*$").captures(&scope.direct)
                && scope.params.contains(&m[1]) {numeric.insert(name.clone());}
    }
    for call in re!(r"(\.?[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)\s*\[\s*`[A-Za-z][A-Za-z0-9_.]*\s*\]").captures_iter(code) {
        let at=call.get(0).unwrap().start();let name=&call[1];if !boundary(code,at){continue;}
        if scope_at(&scopes,at).is_some_and(|s|scopes[s].locals.contains(name)){continue;}
        if numeric.contains(&qualify(name,ns_at(at))) {out.push(Finding::new(path,line_at(code,at),"QT002",format!("Symbol literal passed to numeric function {name}")));}
    }
    for scope in &scopes {
        if scope.parent.is_none()
            || re!(r"\b(?:select|exec|update|delete)\b").is_match(&scope.direct)
        {
            continue;
        }
        let mut outer = HashSet::new();
        let mut parent = scope.parent;
        while let Some(p) = parent {
            outer.extend(scopes[p].locals.iter().cloned());
            parent = scopes[p].parent;
        }
        let direct = re!(r"`[A-Za-z0-9_./:]*")
            .replace_all(&scope.direct, |m: &regex::Captures| " ".repeat(m[0].len()));
        let mut seen = HashSet::new();
        for m in re!(r"[A-Za-z][A-Za-z0-9_]*").find_iter(&direct) {
            let name = m.as_str();
            if !boundary(&direct, m.start())
                || direct[m.end()..].starts_with('.')
                || !outer.contains(name)
                || scope.locals.contains(name)
                || !seen.insert(name)
            {
                continue;
            }
            if globals.contains_key(&qualify(name, &scope.namespace))
                || globals.contains_key(&format!(".{name}"))
            {
                continue;
            }
            out.push(Finding::new(
                path,
                line_at(code, scope.body + m.start()),
                "QF005",
                format!("Nested lambda references enclosing local '{name}' without a parameter"),
            ));
        }
    }
    out
}
