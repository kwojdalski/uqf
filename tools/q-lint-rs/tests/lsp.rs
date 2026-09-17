//! The language server driven the way an editor drives it: a real process,
//! real LSP framing on its stdin and stdout.
//!
//! Unit tests over `serve` with in-memory buffers would miss the two things
//! most likely to break an editor integration - the wire framing, and the
//! process not exiting - because both only exist once there is a process.
use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Read, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
};

struct Server {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl Server {
    fn start() -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_qlinter"))
            .args(["--lsp", "--profile", "uqf"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn qlinter --lsp");
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        Self {
            child,
            stdin,
            stdout,
        }
    }

    fn send(&mut self, value: Value) {
        let body = serde_json::to_vec(&value).unwrap();
        write!(self.stdin, "Content-Length: {}\r\n\r\n", body.len()).unwrap();
        self.stdin.write_all(&body).unwrap();
        self.stdin.flush().unwrap();
    }

    fn receive(&mut self) -> Value {
        let mut length = 0;
        loop {
            let mut line = String::new();
            assert!(
                self.stdout.read_line(&mut line).unwrap() > 0,
                "server closed stdout while a reply was expected"
            );
            if line == "\r\n" {
                break;
            }
            if let Some(v) = line.strip_prefix("Content-Length: ") {
                length = v.trim().parse().unwrap();
            }
        }
        let mut body = vec![0; length];
        self.stdout.read_exact(&mut body).unwrap();
        serde_json::from_slice(&body).unwrap()
    }

    /// Read until a publishDiagnostics for this uri arrives.
    fn diagnostics_for(&mut self, uri: &str) -> Vec<Value> {
        for _ in 0..10 {
            let message = self.receive();
            if message["method"] == "textDocument/publishDiagnostics"
                && message["params"]["uri"] == uri
            {
                return message["params"]["diagnostics"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default();
            }
        }
        panic!("no diagnostics for {uri}");
    }

    fn initialize(&mut self) -> Value {
        self.send(
            json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}),
        );
        let reply = self.receive();
        self.send(json!({"jsonrpc":"2.0","method":"initialized","params":{}}));
        reply
    }

    fn open(&mut self, uri: &str, text: &str) {
        self.send(json!({"jsonrpc":"2.0","method":"textDocument/didOpen",
            "params":{"textDocument":{"uri":uri,"languageId":"q","version":1,"text":text}}}));
    }

    fn shutdown_and_exit(&mut self) -> i32 {
        self.send(json!({"jsonrpc":"2.0","id":99,"method":"shutdown"}));
        let reply = self.receive();
        assert_eq!(reply["id"], 99, "shutdown must be answered");
        self.send(json!({"jsonrpc":"2.0","method":"exit"}));
        self.child.wait().unwrap().code().unwrap_or(-1)
    }
}

/// `desc` is a q builtin, so this is a reserved-parameter finding (QF001) -
/// the same trap this repository has hit in real code more than once.
const RESERVED_PARAM: &str = "f:{[desc] 1+1}\n";

#[test]
fn initialize_announces_full_document_sync() {
    // An editor reads this to decide what to send on every keystroke. Getting
    // it wrong means either no updates or updates the server cannot apply.
    let mut server = Server::start();
    let reply = server.initialize();
    assert_eq!(reply["result"]["capabilities"]["textDocumentSync"], 1);
    assert_eq!(reply["result"]["serverInfo"]["name"], "q-lint");
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn opening_a_document_publishes_its_findings() {
    let mut server = Server::start();
    server.initialize();
    server.open("file:///tmp/probe.q", RESERVED_PARAM);
    let diagnostics = server.diagnostics_for("file:///tmp/probe.q");
    assert_eq!(diagnostics.len(), 1, "one finding: {diagnostics:?}");
    assert_eq!(diagnostics[0]["code"], "QF001");
    assert_eq!(diagnostics[0]["source"], "q-lint");
    assert_eq!(diagnostics[0]["severity"], 2);
    // Zero-based, unlike the finding's own 1-based line.
    assert_eq!(diagnostics[0]["range"]["start"]["line"], 0);
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn a_change_lints_the_buffer_rather_than_the_file_on_disk() {
    // THE REASON THIS IS A SERVER. The URI names a file that does not exist;
    // the text only ever lived in the editor. A CLI over paths cannot do this.
    let mut server = Server::start();
    server.initialize();
    server.open("file:///tmp/never-written.q", "f:{[x] x}\n");
    assert!(
        server
            .diagnostics_for("file:///tmp/never-written.q")
            .is_empty(),
        "clean buffer starts with no findings"
    );
    server.send(json!({"jsonrpc":"2.0","method":"textDocument/didChange",
        "params":{"textDocument":{"uri":"file:///tmp/never-written.q","version":2},
                  "contentChanges":[{"text": RESERVED_PARAM}]}}));
    let diagnostics = server.diagnostics_for("file:///tmp/never-written.q");
    assert_eq!(diagnostics.len(), 1, "the edited text is what gets linted");
    assert_eq!(diagnostics[0]["code"], "QF001");
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn the_last_change_wins_when_a_client_batches_edits() {
    // A client may coalesce edits into one notification. Taking the first
    // would lint a buffer the user has already moved past, and the symptom -
    // diagnostics one keystroke stale - is easy to mistake for lag.
    let mut server = Server::start();
    server.initialize();
    server.open("file:///tmp/batched.q", RESERVED_PARAM);
    server.diagnostics_for("file:///tmp/batched.q");
    server.send(json!({"jsonrpc":"2.0","method":"textDocument/didChange",
        "params":{"textDocument":{"uri":"file:///tmp/batched.q","version":2},
                  "contentChanges":[{"text": RESERVED_PARAM},{"text":"f:{[x] x}\n"}]}}));
    assert!(
        server.diagnostics_for("file:///tmp/batched.q").is_empty(),
        "the final text is clean, so the findings clear"
    );
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn closing_a_document_clears_its_diagnostics() {
    // An empty list, not silence: the server owns these until it says
    // otherwise, so a closed file would keep its squiggles in the problems
    // panel for the rest of the session.
    let mut server = Server::start();
    server.initialize();
    server.open("file:///tmp/closing.q", RESERVED_PARAM);
    assert_eq!(server.diagnostics_for("file:///tmp/closing.q").len(), 1);
    server.send(json!({"jsonrpc":"2.0","method":"textDocument/didClose",
        "params":{"textDocument":{"uri":"file:///tmp/closing.q"}}}));
    assert!(server.diagnostics_for("file:///tmp/closing.q").is_empty());
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn a_percent_encoded_uri_reaches_the_rules_as_a_real_path() {
    // Without decoding, a path with a space arrives as %20 and the
    // filename-based rules see a file that does not exist.
    let mut server = Server::start();
    server.initialize();
    server.open("file:///tmp/a%20folder/probe.q", RESERVED_PARAM);
    let diagnostics = server.diagnostics_for("file:///tmp/a%20folder/probe.q");
    assert_eq!(diagnostics.len(), 1, "the document is still linted");
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn an_unsupported_request_is_refused_rather_than_ignored() {
    // A request with no reply looks like a hung server to every client.
    let mut server = Server::start();
    server.initialize();
    server.send(json!({"jsonrpc":"2.0","id":7,"method":"textDocument/completion","params":{}}));
    let reply = server.receive();
    assert_eq!(reply["id"], 7);
    assert_eq!(reply["error"]["code"], -32601);
    assert_eq!(server.shutdown_and_exit(), 0);
}

#[test]
fn exit_without_shutdown_reports_a_nonzero_code() {
    // The protocol's own rule, and the only way a supervisor can tell a
    // clean stop from an editor that died.
    let mut server = Server::start();
    server.initialize();
    server.send(json!({"jsonrpc":"2.0","method":"exit"}));
    assert_eq!(server.child.wait().unwrap().code().unwrap_or(-1), 1);
}
