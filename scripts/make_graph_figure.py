"""Draw the regulation graph as a figure, for a slide or a README.

    python scripts/make_graph_figure.py

Writes two images under out/figures:

    reg_graph_overview.png   the whole regulation, and the 15 rules inside it
    reg_graph_rule.png       one rule, and everything the graph knows about it

The overview is the scale picture. The regulation is 2,102 sections and the rule
set names 15 of them, and that ratio is the honest headline of this project: the
tool covers a slice of the regulation, and the graph is what makes the size of
that slice measurable rather than a guess.

The layout is computed here rather than by a force solver. A force layout of
2,102 mostly tree shaped nodes is a hairball that says nothing, and it lands
somewhere different on every run. The regulation has a real structure, so the
sections are placed on a ring in document order with depth as radius, which is
stable, reproducible and readable: the numbering runs clockwise from the top and
a reader can find a section on it.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

from septic import config
from septic.report.assets import TOKENS
from septic.rules.graph import load_graph

C = TOKENS["colour"]

# One colour per kind of node, taken from the palette the console and the report
# already use, so a slide sits beside a screenshot without a clash.
NODE_COLOUR = {
    "Section": C["line"],
    "Exhibit": C["remedy_fg"],
    "Definition": C["unverified_edge"],
    "Rule": C["clear_edge"],
}
EDGE_COLOUR = {
    "CONTAINS": C["line"],
    "REFERENCES": C["remedy_fg"],
    "EXCEPTION": C["deficiency_edge"],
    "USES_TERM": C["unverified_edge"],
    "DEFINES": C["unverified_edge"],
    "CITES": C["clear_edge"],
}


def section_key(number: str) -> tuple:
    """Sort key for a section number, so 5.10 follows 5.9 rather than 5.1."""
    parts = []
    for piece in str(number).split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def ring_positions(graph):
    """Place every node. Sections on a ring in document order, rules inside.

    Radius carries depth, so a top level section sits on the inner edge of the
    ring and a deeply numbered subsection on the outer edge. Angle carries
    document order. Both are deterministic: the same graph draws the same figure
    every time, which a force layout does not.
    """
    sections = [
        (n, d) for n, d in graph.nodes(data=True) if d.get("type") == "Section"
    ]
    sections.sort(key=lambda item: section_key(item[1].get("number", "0")))

    positions: dict[str, tuple[float, float]] = {}
    angles: dict[str, float] = {}
    total = max(len(sections), 1)
    for index, (node, data) in enumerate(sections):
        # Clockwise from the top, so the numbering reads the way a page does.
        angle = math.pi / 2 - (2 * math.pi * index / total)
        depth = str(data.get("number", "")).count(".")
        radius = 1.0 + 0.085 * min(depth, 5)
        angles[node] = angle
        positions[node] = (radius * math.cos(angle), radius * math.sin(angle))

    # Exhibits and definitions have no place in the numbering, so they get their
    # own outer arcs rather than being forced into it.
    for kind, radius, start, end in (
        ("Exhibit", 1.62, math.pi * 0.08, math.pi * 0.92),
        ("Definition", 1.62, math.pi * 1.08, math.pi * 1.92),
    ):
        members = sorted(
            n for n, d in graph.nodes(data=True) if d.get("type") == kind
        )
        for index, node in enumerate(members):
            span = (end - start) / max(len(members) - 1, 1)
            angle = start + span * index
            angles[node] = angle
            positions[node] = (radius * math.cos(angle), radius * math.sin(angle))

    # The rules sit on their own ring inside the sections, spaced evenly. Placing
    # each one at the angle of what it cites was the obvious idea and it made a
    # worse picture: six of the fifteen cite Exhibit C, so most of them landed in
    # one wedge and the figure read as lopsided rather than as covering a
    # document. Even spacing keeps the spokes distinguishable.
    rules = sorted(
        n for n, d in graph.nodes(data=True) if d.get("type") == "Rule"
    )
    for index, node in enumerate(rules):
        angle = math.pi / 2 - (2 * math.pi * index / max(len(rules), 1))
        angles[node] = angle
        positions[node] = (0.80 * math.cos(angle), 0.80 * math.sin(angle))

    return positions


def curved(ax, start, end, colour, width, alpha, bow=0.25, zorder=1):
    """An edge drawn as an arc, so a chord across the ring stays legible."""
    ax.add_patch(FancyArrowPatch(
        start, end, connectionstyle="arc3,rad=" + str(bow), arrowstyle="-",
        color=colour, linewidth=width, alpha=alpha, zorder=zorder,
    ))


def draw_overview(graph, out_path: Path) -> Path:
    positions = ring_positions(graph)
    counts = {
        kind: sum(1 for _, d in graph.nodes(data=True) if d.get("type") == kind)
        for kind in ("Section", "Exhibit", "Definition", "Rule")
    }

    fig, ax = plt.subplots(figsize=(13, 13), dpi=170)
    ax.set_facecolor(C["surface"])
    fig.patch.set_facecolor(C["surface"])

    # Containment first and faintest. It is the shape of the document, not the
    # thing being shown, and at 2,050 edges it drowns anything drawn under it.
    for a, b, data in graph.edges(data=True):
        if data.get("type") != "CONTAINS":
            continue
        if a not in positions or b not in positions:
            continue
        ax.plot(
            [positions[a][0], positions[b][0]],
            [positions[a][1], positions[b][1]],
            color=EDGE_COLOUR["CONTAINS"], linewidth=0.3, alpha=0.5, zorder=1,
        )

    # Cross references as chords. These are what make it a graph rather than an
    # outline: a section that cannot be read without another section.
    #
    # Only REFERENCES and EXCEPTION are drawn. The graph also holds 606
    # USES_TERM edges, and drawing those buried everything else under a wash of
    # lines from nineteen definitions, which is a true fact about the data and a
    # useless picture. The caption says they are left out.
    for a, b, data in graph.edges(data=True):
        kind = data.get("type")
        if kind not in ("REFERENCES", "EXCEPTION"):
            continue
        if a not in positions or b not in positions:
            continue
        curved(ax, positions[a], positions[b], EDGE_COLOUR.get(kind, C["line"]),
               width=0.5, alpha=0.45, bow=0.28, zorder=2)

    # Nodes.
    for kind, size, zorder in (
        ("Section", 5.5, 3), ("Exhibit", 30, 4), ("Definition", 26, 4),
    ):
        xs, ys = [], []
        for node, data in graph.nodes(data=True):
            if data.get("type") == kind and node in positions:
                xs.append(positions[node][0])
                ys.append(positions[node][1])
        ax.scatter(xs, ys, s=size, c=NODE_COLOUR[kind], zorder=zorder,
                   linewidths=0, alpha=1.0)

    # The rules, and the spokes to what they cite. Drawn last and heaviest,
    # because they are the point of the picture.
    for node, data in graph.nodes(data=True):
        if data.get("type") != "Rule" or node not in positions:
            continue
        for _, target, edge in graph.out_edges(node, data=True):
            if edge.get("type") != "CITES" or target not in positions:
                continue
            ax.plot(
                [positions[node][0], positions[target][0]],
                [positions[node][1], positions[target][1]],
                color=EDGE_COLOUR["CITES"], linewidth=1.5, alpha=0.85, zorder=5,
            )
            ax.scatter([positions[target][0]], [positions[target][1]], s=95,
                       c=EDGE_COLOUR["CITES"], zorder=6, linewidths=0)
        ax.scatter([positions[node][0]], [positions[node][1]], s=70,
                   c=NODE_COLOUR["Rule"], zorder=7,
                   edgecolors=C["surface"], linewidths=1.2)

    ax.set_aspect("equal")
    ax.axis("off")
    limit = 1.78
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)

    # A clear field for the caption. Fifteen spokes converge near the middle and
    # they ran straight through the text, so the text is given ground to sit on
    # rather than being moved somewhere it explains nothing.
    ax.add_patch(plt.Circle(
        (0, 0), 0.40, facecolor=C["surface"], edgecolor="none",
        zorder=8, alpha=0.94,
    ))

    # The count in the middle is the number of distinct provisions the rule set
    # cites, not the number of rules. Fifteen rules cite nine places, because six
    # of them cite Exhibit C, and writing 15 in the middle of a picture of the
    # regulation would claim fifteen provisions are covered.
    cited = {
        b for _, b, d in graph.edges(data=True) if d.get("type") == "CITES"
    }
    ax.text(0, 0.20, format(counts["Section"], ","), ha="center", va="center",
            fontsize=46, fontweight="bold", color=C["ink"], zorder=9)
    ax.text(0, 0.10, "SECTIONS AND EXHIBITS", ha="center", va="center",
            fontsize=12, color=C["muted"], zorder=9)
    ax.text(0, -0.06, str(counts["Rule"]) + " rules cite " + str(len(cited)),
            ha="center", va="center", fontsize=15,
            color=EDGE_COLOUR["CITES"], fontweight="bold", zorder=9)
    ax.text(0, -0.17, "of them", ha="center", va="center", fontsize=12,
            color=C["muted"], zorder=9)

    ax.set_title(
        "Delaware on-site wastewater regulation, as a graph",
        fontsize=21, fontweight="bold", color=C["ink"], pad=18,
    )

    # Nodes are dots and edges are lines, in the legend as on the figure. The
    # first version used a dot for both and the definition colour and the
    # defined-term colour were indistinguishable.
    dots = [
        (NODE_COLOUR["Section"], "Section (" + format(counts["Section"], ",") + ")"),
        (NODE_COLOUR["Exhibit"], "Exhibit (" + str(counts["Exhibit"]) + ")"),
        (NODE_COLOUR["Definition"], "Definition (" + str(counts["Definition"]) + ")"),
        (NODE_COLOUR["Rule"], "Rule (" + str(counts["Rule"]) + ")"),
    ]
    lines = [
        (EDGE_COLOUR["CITES"], "A rule cites this"),
        (EDGE_COLOUR["REFERENCES"], "References another section"),
        (EDGE_COLOUR["EXCEPTION"], "Carries an exception"),
    ]
    handles = [
        plt.Line2D([], [], marker="o", linestyle="", markersize=9,
                   markerfacecolor=colour, markeredgecolor="none", label=label)
        for colour, label in dots
    ] + [
        plt.Line2D([], [], color=colour, linewidth=2.2, label=label)
        for colour, label in lines
    ]
    ax.legend(handles=handles, loc="lower center", ncol=4, frameon=False,
              fontsize=11, bbox_to_anchor=(0.5, -0.055))

    fig.text(
        0.5, 0.022,
        "Sections run clockwise from the top in document order, and radius is "
        "depth in the numbering. The 606 defined-term edges are left out:"
        " drawn, they cover the figure.\n"
        "Every threshold in the rule set traces to a section and a page of "
        "the 2014 regulation.",
        ha="center", fontsize=10.5, color=C["muted"], linespacing=1.5,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=C["surface"])
    plt.close(fig)
    return out_path


def rule_label(data, node):
    """Short label for a rule node, the identifier without the description."""
    rule_id = str(data.get("rule_id", node))
    pieces = rule_id.split("-")
    if len(pieces) >= 2:
        return pieces[0] + "-" + pieces[1]
    return rule_id


def neighbourhood(graph, rule_node: str) -> set:
    """The rule, what it cites, and what reading that requires."""
    neighbours = {rule_node}
    for _, b in graph.out_edges(rule_node):
        neighbours.add(b)
        for _, c, e in graph.out_edges(b, data=True):
            if e.get("type") in ("USES_TERM", "REFERENCES", "EXCEPTION"):
                neighbours.add(c)
        for a, _, e in graph.in_edges(b, data=True):
            if e.get("type") in ("REFERENCES", "CONTAINS"):
                neighbours.add(a)
    return neighbours


def draw_rule_detail(graph, rule_node: str, out_path: Path):
    """One rule and its neighbourhood, with every node labelled."""
    if rule_node not in graph:
        return None

    sub = graph.subgraph(neighbourhood(graph, rule_node))

    import networkx as nx

    # k is tuned for a handful of nodes. The default spreads ten nodes into the
    # corners of a wide figure and leaves the middle empty.
    positions = nx.spring_layout(sub, seed=11, k=0.55, iterations=400)

    fig, ax = plt.subplots(figsize=(13, 8.5), dpi=170)
    ax.set_facecolor(C["surface"])
    fig.patch.set_facecolor(C["surface"])

    for a, b, data in sub.edges(data=True):
        kind = data.get("type", "CONTAINS")
        curved(ax, positions[a], positions[b], EDGE_COLOUR.get(kind, C["line"]),
               width=1.6 if kind == "CITES" else 1.0,
               alpha=0.9 if kind == "CITES" else 0.5, bow=0.12)

    for node in sub.nodes():
        data = sub.nodes[node]
        kind = data.get("type", "Section")
        is_rule = kind == "Rule"
        ax.scatter([positions[node][0]], [positions[node][1]],
                   s=420 if is_rule else 240,
                   c=NODE_COLOUR.get(kind, C["line"]), zorder=5,
                   edgecolors=C["surface"], linewidths=1.6)
        if is_rule:
            label = rule_label(data, node)
        elif kind == "Definition":
            label = data.get("term", node.split(":", 1)[-1])
        elif kind == "Exhibit":
            label = "Exhibit " + str(data.get("letter", node.split(":", 1)[-1]))
        else:
            label = data.get("number", node.split(":", 1)[-1])
        ax.annotate(
            str(label)[:34], positions[node], textcoords="offset points",
            xytext=(0, 13), ha="center", fontsize=9.5, color=C["ink"],
            zorder=6,
        )

    rule = graph.nodes[rule_node]
    ax.set_title(
        str(rule.get("rule_id", rule_node))
        + ": what the graph knows about one rule",
        fontsize=18, fontweight="bold", color=C["ink"], pad=16,
    )
    fig.text(
        0.5, 0.02,
        str(rule.get("description", "")) + "\nCited at "
        + str(rule.get("citation_section", "")) + ", page "
        + str(rule.get("citation_page", ""))
        + ". Every cross reference and defined term above was resolved before "
          "the threshold was accepted.",
        ha="center", fontsize=10.5, color=C["muted"], linespacing=1.5,
    )
    ax.axis("off")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=C["surface"])
    plt.close(fig)
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the regulation graph")
    parser.add_argument("--rule", default=None,
                        help="Rule node to detail, default the first one")
    args = parser.parse_args(argv)

    graph = load_graph()
    figures = config.OUT_DIR / "figures"

    overview = draw_overview(graph, figures / "reg_graph_overview.png")
    print("wrote " + str(overview))

    rule_node = args.rule
    if rule_node is None:
        # The rule with the most around it, rather than the first alphabetically.
        # FLOW-001 came first and its neighbourhood is four nodes, which makes a
        # figure that shows nothing about why a graph was needed.
        rules = sorted(
            n for n, d in graph.nodes(data=True) if d.get("type") == "Rule"
        )
        rule_node = max(
            rules, key=lambda r: (len(neighbourhood(graph, r)), r)
        ) if rules else None
    if rule_node:
        detail = draw_rule_detail(graph, rule_node, figures / "reg_graph_rule.png")
        if detail:
            print("wrote " + str(detail))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
