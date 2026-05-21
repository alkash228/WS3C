from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

from samv.config import WORKER_COUNT

T = TypeVar("T")
R = TypeVar("R")


def worker_count() -> int:

    """Сколько воркеров в пуле."""
    if WORKER_COUNT > 0:
        return max(1, WORKER_COUNT)
    cpus = os.cpu_count() or 2
    return max(1, min(16, cpus - 1))


def map_parallel(
    func: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int | None = None,
    chunksize: int = 1,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[R]:

    """Гоняем функцию по списку в процессах."""
    seq = list(items)
    if not seq:
        return []
    workers = max_workers if max_workers is not None else worker_count()
    if len(seq) == 1 or workers <= 1:
        out: list[R] = []
        total = len(seq)
        for i, item in enumerate(seq):
            out.append(func(item))
            if on_progress:
                on_progress(i + 1, total)
        return out

    results: list[R] = [None] * len(seq)  # type: ignore[list-item]
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(func, item): idx
            for idx, item in enumerate(seq)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
            done += 1
            if on_progress:
                on_progress(done, len(seq))
    return results


def run_parallel_threads(
    jobs: list[tuple[str, Callable[[], object]]],
) -> dict[str, object]:

    """Несколько задач в потоках."""
    if not jobs:
        return {}
    if len(jobs) == 1:
        key, fn = jobs[0]
        return {key: fn()}

    out: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=min(len(jobs), worker_count())) as pool:
        futs = {pool.submit(fn): key for key, fn in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            out[key] = fut.result()
    return out
