import networkx as nx  # type: ignore

from asyncio import PriorityQueue, Lock, Condition
from typing import Dict, Set, List, Generator, Optional

from .graph import UniqueId
from dbt.contracts.graph.parsed import ParsedSourceDefinition, ParsedExposure, ParsedMetric
from dbt.contracts.graph.compiled import GraphMemberNode
from dbt.contracts.graph.manifest import Manifest
from dbt.node_types import NodeType


class GraphQueue:
    """A fancy queue that is backed by the dependency graph.
    Note: this will mutate input!

    This queue is thread-safe for `mark_done` calls, though you must ensure
    that separate threads do not call `.empty()` or `size()` and `.get()` at
    the same time, as there is an unlocked race!
    """

    def __init__(self, graph: nx.DiGraph, manifest: Manifest, selected: Set[UniqueId]):
        self.graph = graph
        self.manifest = manifest
        self._selected = selected
        # store the queue as a priority queue.
        self.inner: PriorityQueue = PriorityQueue()
        # things that have been popped off the queue but not finished
        # and worker thread reservations
        self.in_progress: Set[UniqueId] = set()
        # things that are in the queue
        self.queued: Set[UniqueId] = set()
        # this lock controls most things
        self.lock = Lock()
        # store the 'score' of each node as a number. Lower is higher priority.
        self._scores = self._get_scores(self.graph)
        # populate the initial queue
        self._find_new_additions()
        # awaits after task end
        self.some_task_done = Condition(self.lock)

    def get_selected_nodes(self) -> Set[UniqueId]:
        return self._selected.copy()

    def _include_in_cost(self, node_id: UniqueId) -> bool:
        node = self.manifest.expect(node_id)
        if node.resource_type != NodeType.Model:
            return False
        # must be a Model - tell mypy this won't be a Source or Exposure or Metric
        assert not isinstance(node, (ParsedSourceDefinition, ParsedExposure, ParsedMetric))
        if node.is_ephemeral:
            return False
        return True

    @staticmethod
    def _grouped_topological_sort(
        graph: nx.DiGraph,
    ) -> Generator[List[str], None, None]:
        """Topological sort of given graph that groups ties.

        Adapted from `nx.topological_sort`, this function returns a topo sort of a graph however
        instead of arbitrarily ordering ties in the sort order, ties are grouped together in
        lists.

        Args:
            graph: The graph to be sorted.

        Returns:
            A generator that yields lists of nodes, one list per graph depth level.
        """
        indegree_map = {v: d for v, d in graph.in_degree() if d > 0}
        zero_indegree = [v for v, d in graph.in_degree() if d == 0]

        while zero_indegree:
            yield zero_indegree
            new_zero_indegree = []
            for v in zero_indegree:
                for _, child in graph.edges(v):
                    indegree_map[child] -= 1
                    if not indegree_map[child]:
                        new_zero_indegree.append(child)
            zero_indegree = new_zero_indegree

    def _get_scores(self, graph: nx.DiGraph) -> Dict[str, int]:
        """Scoring nodes for processing order.

        Scores are calculated by the graph depth level. Lowest score (0) should be processed first.

        Args:
            graph: The graph to be scored.

        Returns:
            A dictionary consisting of `node name`:`score` pairs.
        """
        # split graph by connected subgraphs
        subgraphs = (graph.subgraph(x) for x in nx.connected_components(nx.Graph(graph)))

        # score all nodes in all subgraphs
        scores = {}
        for subgraph in subgraphs:
            grouped_nodes = self._grouped_topological_sort(subgraph)
            for level, group in enumerate(grouped_nodes):
                for node in group:
                    scores[node] = level

        return scores

    async def get(self) -> GraphMemberNode:
        """Get a node off the inner priority queue. By default, this blocks.

        This takes the lock, but only for part of it.

        :param block: If True, block until the inner queue has data
        :param timeout: If set, block for timeout seconds waiting for data.
        :return: The node as present in the manifest.

        See `queue.PriorityQueue` for more information on `get()` behavior and
        exceptions.
        """
        _, node_id = await self.inner.get()
        async with self.lock:
            self._mark_in_progress(node_id)
        return self.manifest.expect(node_id)

    async def size(self) -> int:
        """The size of the queue is the number of tasks left for the queue to
        give out, regardless of where they are. Incomplete tasks are not part
        of the length.
        """
        return self.inner.qsize()

    async def empty(self) -> bool:
        """The graph queue is 'empty' if it all remaining nodes in the graph
        are in progress.
        """
        return self.inner.empty()

    def _already_known(self, node: UniqueId) -> bool:
        """Decide if a node is already known (either handed out as a task, or
        in the queue).

        Callers must hold the lock.

        :param str node: The node ID to check
        :returns bool: If the node is in progress/queued.
        """
        return node in self.in_progress or node in self.queued

    def _find_new_additions(self) -> None:
        """Find any nodes in the graph that need to be added to the internal
        queue and add them.
        """
        for node, in_degree in self.graph.in_degree():
            if not self._already_known(node) and in_degree == 0:
                self.inner.put_nowait((self._scores[node], node))
                self.queued.add(node)

    async def mark_done(self, node_id: UniqueId) -> None:
        """Given a node's unique ID, mark it as done.

        This method takes the lock.

        :param str node_id: The node ID to mark as complete.
        """
        async with self.lock:
            self.in_progress.remove(node_id)
            self.graph.remove_node(node_id)
            self._find_new_additions()
            self.inner.task_done()
            self.some_task_done.notify_all()

    def _mark_in_progress(self, node_id: UniqueId) -> None:
        """Mark the node as 'in progress'.

        Callers must hold the lock.

        :param str node_id: The node ID to mark as in progress.
        """
        self.queued.remove(node_id)
        self.in_progress.add(node_id)

    async def join(self) -> None:
        """Join the queue. Blocks until all tasks are marked as done.

        Make sure not to call this before the queue reports that it is empty.
        """
        await self.inner.join()

    async def wait_until_something_was_done(self) -> int:
        """Block until a task is done, then return the number of unfinished
        tasks.
        """
        async with self.lock:
            await self.some_task_done.wait()
            import ipdb; ipdb.set_trace()
            return self.inner.qsize()
