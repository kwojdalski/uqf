//! The language server: `qlinter --lsp`, speaking LSP over stdin/stdout.
//!
//! WHY A SERVER AND NOT A VS CODE EXTENSION THAT SHELLS OUT
//!
//! Both work, and shelling out is less code. A server is worth the difference
//! because the protocol is the portable part: one binary serves VS Code,
//! Neovim, Helix, Zed and anything else that speaks LSP, and the editor plugin
//! shrinks to "launch this process". An extension that parses `--format json`
//! is a VS Code-only asset that every other editor would have to rewrite.
//!
//! It also fixes the thing a CLI cannot do well: linting a buffer that has
//! never been saved. `didChange` carries the text, so what is checked is what
//! is on screen rather than what is on disk.
//!
//! SYNCHRONOUS, SINGLE-THREADED, AND NO ASYNC RUNTIME
//!
//! tower-lsp is the usual answer and brings tokio with it. This server does
//! one thing - lint a document and publish the result - and `lint` is measured
//! in single-digit milliseconds on a whole file, so there is no operation long
//! enough to be worth yielding for. A request loop that reads, dispatches and
//! replies in order is the whole design, and it keeps this crate's dependency
//! tree exactly as it was.
//!
//! WHAT IT IMPLEMENTS, AND WHAT IT DELIBERATELY DOES NOT
//!
//! initialize/initialized, didOpen/didChange/didSave/didClose, shutdown/exit,
//! and diagnostics pushed with textDocument/publishDiagnostics. That is the
//! set an editor needs to show squiggles.
//!
//! Not here: completion, hover, go-to-definition, formatting. Those need a
//! resolver and a symbol table this crate does not have - it reads source text
//! without executing it, which is the property that makes it safe to run on
//! every keystroke. Claiming those capabilities and answering emptily is worse
//! than not claiming them: an editor that is told a server provides completion
//! stops offering its own word-based fallback.
use crate::jsonrpc::{receive, send};
use q_lint_rs::{Finding, lint};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    io::{BufRead, Write},
};

/// Full document sync (`TextDocumentSyncKind.Full`): every change carries the
/// whole buffer. Incremental sync would save bytes on a large file and cost a
/// patch-application routine whose bugs present as "the linter is looking at
/// text that is not on screen". At these file sizes the bytes are not worth it.
const SYNC_FULL: i64 = 1;

const METHOD_NOT_FOUND: i64 = -32601;
const INVALID_REQUEST: i64 = -32600;

/// Open documents, by URI. The editor is the authority on their contents from
/// didOpen until didClose, so nothing here reads from disk.
type Documents = HashMap<String, String>;

pub fn serve(reader: &mut impl BufRead, writer: &mut impl Write, uqf: bool) -> Result<u8, String> {
    let mut docs: Documents = HashMap::new();
    let mut shutdown_requested = false;
    loop {
        let Some(message) = receive(reader)? else {
            // The stream closed without `exit`. Editors are supposed to send
            // it; when one dies instead, leaving cleanly is the friendlier
            // behaviour and matches what rust-analyzer does.
            return Ok(if shutdown_requested { 0 } else { 1 });
        };
        let method = message["method"].as_str().unwrap_or("").to_string();
        let id = message.get("id").cloned();
        let params = message.get("params").cloned().unwrap_or(Value::Null);

        match (method.as_str(), id) {
            ("initialize", Some(id)) => send(writer, json!({"id": id, "result": capabilities()}))?,
            ("shutdown", Some(id)) => {
                shutdown_requested = true;
                docs.clear();
                send(writer, json!({"id": id, "result": Value::Null}))?;
            }
            ("exit", _) => return Ok(if shutdown_requested { 0 } else { 1 }),

            // A request after shutdown is the one protocol error worth
            // answering rather than ignoring: the client is confused and
            // should be told, not left waiting for a reply that never comes.
            (_, Some(id)) if shutdown_requested => send(
                writer,
                json!({"id": id, "error": {"code": INVALID_REQUEST, "message": "server is shut down"}}),
            )?,

            ("textDocument/didOpen", None) => {
                let uri = uri_of(&params["textDocument"]);
                let text = params["textDocument"]["text"].as_str().unwrap_or("");
                publish(writer, &mut docs, uri, text.to_string(), uqf)?;
            }
            ("textDocument/didChange", None) => {
                let uri = uri_of(&params["textDocument"]);
                // Full sync, so the LAST change holds the whole buffer. Taking
                // the first would silently lint a stale document whenever a
                // client batched two edits into one notification.
                if let Some(text) = params["contentChanges"]
                    .as_array()
                    .and_then(|c| c.last())
                    .and_then(|c| c["text"].as_str())
                {
                    publish(writer, &mut docs, uri, text.to_string(), uqf)?;
                }
            }
            ("textDocument/didSave", None) => {
                // `text` is present only when the client registered for it.
                // Falling back to what we already hold keeps a save from
                // clearing the diagnostics of a document we know the text of.
                let uri = uri_of(&params["textDocument"]);
                let text = params["text"]
                    .as_str()
                    .map(str::to_string)
                    .or_else(|| docs.get(&uri).cloned());
                if let Some(text) = text {
                    publish(writer, &mut docs, uri, text, uqf)?;
                }
            }
            ("textDocument/didClose", None) => {
                let uri = uri_of(&params["textDocument"]);
                docs.remove(&uri);
                // An empty list, not silence: diagnostics are owned by the
                // server until it says otherwise, so a closed file would keep
                // its squiggles in the problems panel forever.
                send(
                    writer,
                    json!({"method": "textDocument/publishDiagnostics",
                           "params": {"uri": uri, "diagnostics": []}}),
                )?;
            }

            // Every other REQUEST gets an error, because a client waiting on a
            // reply that never arrives looks like a hung server.
            (_, Some(id)) => send(
                writer,
                json!({"id": id, "error": {"code": METHOD_NOT_FOUND, "message": format!("unsupported method: {method}")}}),
            )?,
            // Every other NOTIFICATION is ignored, which the protocol requires.
            (_, None) => {}
        }
    }
}

fn capabilities() -> Value {
    json!({
        "capabilities": {"textDocumentSync": SYNC_FULL},
        "serverInfo": {"name": "q-lint", "version": env!("CARGO_PKG_VERSION")},
    })
}

fn uri_of(text_document: &Value) -> String {
    text_document["uri"].as_str().unwrap_or("").to_string()
}

/// Lint one document and push its diagnostics.
fn publish(
    writer: &mut impl Write,
    docs: &mut Documents,
    uri: String,
    text: String,
    uqf: bool,
) -> Result<(), String> {
    let path = path_of(&uri);
    let findings = lint(&text, &path, uqf);
    let diagnostics: Vec<Value> = findings.iter().map(|f| diagnostic(f, &text)).collect();
    send(
        writer,
        json!({"method": "textDocument/publishDiagnostics",
               "params": {"uri": uri.clone(), "diagnostics": diagnostics}}),
    )?;
    docs.insert(uri, text);
    Ok(())
}

/// One finding as an LSP diagnostic.
///
/// The two coordinate systems differ in both ways they can: findings are
/// 1-based and LSP is 0-based, and LSP counts UTF-16 code units rather than
/// characters or bytes. Getting the second wrong is invisible in ASCII source
/// and misplaces every marker after the first non-ASCII character in a
/// comment, which is exactly the kind of bug that ships.
fn diagnostic(f: &Finding, source: &str) -> Value {
    let line = f.line.saturating_sub(1);
    let end_line = f.end_line.map_or(line, |l| l.saturating_sub(1));
    let start_char = f.column.map_or(0, |c| c.saturating_sub(1));
    // No column means the rule found a line, not a span. Underlining from the
    // first non-blank character to the end of the line is what a reader means
    // by "this line": starting at 0 would decorate the indentation, and an
    // empty range would show nothing at all in some clients.
    let end_char = match f.end_column {
        Some(c) => c.saturating_sub(1),
        None => utf16_len(line_text(source, end_line)),
    };
    let start_char = if f.column.is_none() {
        indent_utf16(line_text(source, line))
    } else {
        start_char
    };
    json!({
        "range": {
            "start": {"line": line, "character": start_char},
            "end": {"line": end_line, "character": end_char.max(start_char)},
        },
        "severity": severity(&f.severity),
        "code": f.code,
        "source": f.source,
        "message": f.detail,
    })
}

fn severity(name: &str) -> i64 {
    match name {
        "error" => 1,
        "information" | "info" => 3,
        "hint" => 4,
        _ => 2,
    }
}

fn line_text(source: &str, line: usize) -> &str {
    source.lines().nth(line).unwrap_or("")
}

fn utf16_len(text: &str) -> usize {
    text.chars().map(char::len_utf16).sum()
}

fn indent_utf16(text: &str) -> usize {
    utf16_len(&text[..text.len() - text.trim_start().len()])
}

/// A `file:` URI as a filesystem path.
///
/// Hand-rolled rather than pulling in a URL crate: the only scheme an editor
/// sends for a document on disk is `file:`, and the only transformation that
/// matters is percent-decoding - without it every path containing a space
/// arrives as `%20` and the filename-based rules see a file that does not
/// exist. A URI that is not `file:` (an untitled buffer, say) is handed
/// through unchanged, so it still gets linted and simply has no useful path.
fn path_of(uri: &str) -> String {
    let Some(rest) = uri.strip_prefix("file://") else {
        return uri.to_string();
    };
    // Strip the empty authority: file:///a/b has a host of "", so the path
    // begins at the third slash.
    let rest = rest.strip_prefix('/').map_or(rest, |r| r);
    let mut out = String::with_capacity(rest.len() + 1);
    out.push('/');
    let bytes = rest.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%'
            && i + 2 < bytes.len()
            && let Ok(byte) = u8::from_str_radix(&rest[i + 1..i + 3], 16)
        {
            out.push(byte as char);
            i += 3;
            continue;
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percent_escapes_decode_and_other_schemes_pass_through() {
        assert_eq!(path_of("file:///tmp/a%20b/c.q"), "/tmp/a b/c.q");
        assert_eq!(path_of("file:///tmp/plain.q"), "/tmp/plain.q");
        assert_eq!(path_of("untitled:Untitled-1"), "untitled:Untitled-1");
    }

    #[test]
    fn positions_are_zero_based_and_counted_in_utf16() {
        // A line whose comment holds a character outside the BMP: one q char,
        // two UTF-16 code units. A marker that counted characters would stop
        // one unit short of the line end.
        let source = "a:1 / \u{1F600}\n";
        let f = Finding::new("x.q", 1, "QP001", "detail".into());
        let d = diagnostic(&f, source);
        assert_eq!(d["range"]["start"]["line"], 0);
        assert_eq!(d["range"]["end"]["character"], utf16_len("a:1 / \u{1F600}"));
    }

    #[test]
    fn severity_names_map_to_the_protocols_numbers() {
        assert_eq!(severity("error"), 1);
        assert_eq!(severity("warning"), 2);
        assert_eq!(severity("anything else"), 2);
    }
}
