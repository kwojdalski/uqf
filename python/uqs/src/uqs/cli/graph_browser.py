"""`uqs graph --interactive`: the process trees in a Textual app.

Separate from cli/graph.py so Textual is imported only when a browser opens.
The filter box keeps focus throughout, as in table_browser.py, so the keys
that move through the tree are bound at the app with priority.

Filtering draws every path in full (no repeats), then cuts it down: a match
must not hide under a process drawn as `shown above`.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input, Static, Tree
from textual.widgets.tree import TreeNode

from uqs.cli.graph import describe, label, prune
from uqs.model import process_graph
from uqs.model.process_graph import Direction, Edge, Node


def browser(
    processes: list[str],
    edges: Sequence[Edge],
    status: dict[str, str],
    info: dict[str, dict[str, str]],
    direction: Direction,
    root: str | None,
) -> App[str | None]:
    """The app. Its result is the highlighted process's name, or None."""

    class GraphBrowser(App[str | None]):
        TITLE = "uqs graph"
        CSS = """
        #count { height: 1; color: $text-muted; padding: 0 1; }
        Tree { width: 2fr; }
        #detail { width: 1fr; padding: 0 1; border-left: solid $panel; }
        """
        BINDINGS = [
            Binding("escape", "quit_browser", "Quit"),
            Binding("enter", "choose", "Print name", priority=True),
            Binding("ctrl+t", "turn", "Upstream/downstream", priority=True),
            Binding("down", "move('cursor_down')", show=False, priority=True),
            Binding("up", "move('cursor_up')", show=False, priority=True),
            Binding("pagedown", "move('page_down')", show=False, priority=True),
            Binding("pageup", "move('page_up')", show=False, priority=True),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.direction: Direction = direction

        def compose(self) -> ComposeResult:
            yield Input(placeholder="filter: process or table name, fuzzy; space for AND")
            yield Static(id="count")
            with Horizontal():
                yield Tree("processes")
                yield Static(id="detail")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one(Tree).show_root = False
            self.redraw("")
            self.query_one(Input).focus()

        def on_input_changed(self, event: Input.Changed) -> None:
            self.redraw(event.value)

        def redraw(self, query: str) -> None:
            filtering = bool(query.split())
            trees = process_graph.forest(
                processes, edges, self.direction, root=root, collapse=not filtering
            )
            trees = prune(trees, query)
            tree = self.query_one(Tree)
            tree.clear()
            connected, alone = process_graph.unconnected(trees)
            for node in connected:
                self.grow(tree.root, node)
            if alone:
                group = tree.root.add("no declared edges", expand=True)
                for name in alone:
                    self.grow(group, Node(name))
            tree.root.expand_all()
            shown = len({n for t in trees for n in names(t)})
            self.query_one("#count", Static).update(f"{self.direction}: {shown} process(es)")
            self.show_detail(tree.cursor_node)

        def grow(self, parent: TreeNode, node: Node) -> None:
            text = label(node, status, self.direction)
            if node.children:
                branch = parent.add(text, data=node.name, expand=True)
                for child in node.children:
                    self.grow(branch, child)
            else:
                parent.add_leaf(text, data=node.name)

        def show_detail(self, node: TreeNode | None) -> None:
            name = node.data if node is not None else None
            self.query_one("#detail", Static).update(describe(name, info, status) if name else "")

        def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
            self.show_detail(event.node)

        def action_move(self, how: str) -> None:
            tree = self.query_one(Tree)
            {
                "cursor_down": tree.action_cursor_down,
                "cursor_up": tree.action_cursor_up,
                "page_down": tree.action_page_down,
                "page_up": tree.action_page_up,
            }[how]()

        def action_turn(self) -> None:
            self.direction = "upstream" if self.direction == "downstream" else "downstream"
            self.redraw(self.query_one(Input).value)

        def action_choose(self) -> None:
            node = self.query_one(Tree).cursor_node
            if node is not None and node.data:
                self.exit(node.data)

        def action_quit_browser(self) -> None:
            self.exit(None)

    return GraphBrowser()


def names(node: Node) -> set[str]:
    """Every process name in one tree."""
    found = {node.name}
    for child in node.children:
        found |= names(child)
    return found
