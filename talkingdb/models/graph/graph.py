import json
import sqlite3
from dataclasses import dataclass
import networkx as nx
from typing import Type
from smart_slugify import slugify
from networkx.readwrite import json_graph


@dataclass
class GraphModel:
    graph_id: str
    graph: nx.Graph

    @staticmethod
    def make_id(name: str) -> str:
        return f"graph::{slugify(name)}"

    @classmethod
    def create(cls: Type["GraphModel"], graph_id: str, directed: bool = False) -> "GraphModel":
        g = nx.DiGraph() if directed else nx.Graph()
        return cls(graph_id=graph_id, graph=g)

    @classmethod
    def load(cls: Type["GraphModel"], conn: sqlite3.Connection, graph_id: str, directed: bool = False) -> "GraphModel":
        g = nx.DiGraph() if directed else nx.Graph()
        cur = conn.cursor()

        for node_id, attrs in cur.execute(
            "SELECT node_id, attrs FROM nodes WHERE graph_id = ?",
            (graph_id,)
        ):
            g.add_node(node_id, **json.loads(attrs or "{}"))

        for src, dst, attrs in cur.execute(
            "SELECT src, dst, attrs FROM edges WHERE graph_id = ?",
            (graph_id,)
        ):
            g.add_edge(src, dst, **json.loads(attrs or "{}"))

        return cls(graph_id=graph_id, graph=g)

    def save(self, conn: sqlite3.Connection, overwrite: bool = True) -> None:
        cur = conn.cursor()

        if overwrite:
            cur.execute("DELETE FROM nodes WHERE graph_id = ?",
                        (self.graph_id,))
            cur.execute("DELETE FROM edges WHERE graph_id = ?",
                        (self.graph_id,))

        for node_id, attrs in self.graph.nodes(data=True):
            cur.execute(
                "INSERT INTO nodes (graph_id, node_id, attrs) VALUES (?, ?, ?)",
                (self.graph_id, str(node_id), json.dumps(attrs))
            )

        for src, dst, attrs in self.graph.edges(data=True):
            cur.execute(
                "INSERT INTO edges (graph_id, src, dst, attrs) VALUES (?, ?, ?, ?)",
                (self.graph_id, str(src), str(dst), json.dumps(attrs))
            )

        conn.commit()

    def clear(self) -> None:
        self.graph.clear()

    def to_json(self) -> None:
        return {
            "graph_id": self.graph_id,
            "graph": json_graph.node_link_data(self.graph)
        }

    def g_json(self) -> None:
        return json_graph.node_link_data(self.graph)

    @staticmethod
    def init_db(conn: sqlite3.Connection) -> None:
        conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE IF NOT EXISTS nodes (
            graph_id TEXT NOT NULL,
            node_id  TEXT NOT NULL,
            attrs    TEXT,
            PRIMARY KEY (graph_id, node_id)
        );

        CREATE TABLE IF NOT EXISTS edges (
            graph_id TEXT NOT NULL,
            src      TEXT NOT NULL,
            dst      TEXT NOT NULL,
            attrs    TEXT,
            PRIMARY KEY (graph_id, src, dst)
        );

        CREATE INDEX IF NOT EXISTS idx_edges_graph_src
        ON edges(graph_id, src);
        """)
