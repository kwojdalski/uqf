// The whole VS Code integration: launch `qlinter --lsp` and let the protocol
// do the rest.
//
// Everything a reader might expect here - reading files, parsing output,
// building diagnostics, deciding when to re-lint - lives in the server, which
// is why this stays about thirty lines. The same server serves Neovim, Helix
// and Zed with a comparable amount of their own configuration; nothing in this
// file is knowledge those editors would have to reimplement.
import { workspace, window, type ExtensionContext } from "vscode";
import {
  LanguageClient,
  TransportKind,
  type LanguageClientOptions,
  type ServerOptions,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

export async function activate(context: ExtensionContext): Promise<void> {
  const settings = workspace.getConfiguration("q-lint");
  const command = settings.get<string>("serverPath", "qlinter");
  const profile = settings.get<string>("profile", "general");

  const server: ServerOptions = {
    // One entry, used for both: this server has no separate debug mode, and
    // giving it a fabricated one would mean a second thing to keep in step.
    run: { command, args: ["--lsp", "--profile", profile], transport: TransportKind.stdio },
    debug: { command, args: ["--lsp", "--profile", profile], transport: TransportKind.stdio },
  };

  const options: LanguageClientOptions = {
    documentSelector: [{ scheme: "file", language: "q" }],
    // The output channel the server's own trace goes to, so a user debugging
    // the integration has one place to look.
    outputChannelName: "q-lint",
  };

  client = new LanguageClient("q-lint", "q-lint", server, options);
  try {
    await client.start();
  } catch (error) {
    // Say which binary was not found. "Couldn't start the server" sends
    // someone to the logs; naming the path and the setting tells them what to
    // fix, and the commonest cause by far is that qlinter is not on PATH.
    client = undefined;
    window.showErrorMessage(
      `q-lint: could not start "${command}". Set q-lint.serverPath to the qlinter binary ` +
        `(cargo build --release leaves it in tools/q-lint-rs/target/release/qlinter). ${error}`,
    );
  }
  context.subscriptions.push({ dispose: () => void client?.stop() });
}

export function deactivate(): Thenable<void> | undefined {
  return client?.stop();
}
