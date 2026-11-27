# 3. Persistent sync backlog

Status: accepted

## Context

A sync run lists everything that changed on the wiki since the cursor, then fetches each changed page over a rate-limited, interruptible connection. Listing is cheap and bounded; fetching is the slow part, it can fail per page, and it can be cancelled or killed at any moment. If fetching drove straight off an in-memory listing, a crash or a stop would lose the knowledge that those pages still need fetching, and the next run would only see changes newer than the already-advanced cursor. That's a silent gap, which is the worst kind.

## Decision

Split sync into two phases with a durable queue between them.

1. List the changes and write them to a `backlog` table, advancing the `last_ts` cursor, all in one transaction. The backlog row is the unit of outstanding work.
2. Drain the backlog up to `cap` pages per run. Each fetched page is stored and its backlog row deleted in one transaction. Pages that fail stay queued.

Order out of the backlog is: pages the user has actually read first, then new pages, then everything else, newest first within each group. `enqueue` merges duplicates, keeping the strongest priority and newest revision.

## Consequences

- The cursor only advances once the changes it covers are safely in the backlog, so an interrupted fetch phase never loses work. The next run re-drains the backlog before it lists anything new. The recovery doc has the exact interruption cases and the tests that cover them.
- Re-processing is idempotent. Storing a page is an upsert keyed by path, and a revision that's already stored is skipped by a revision-id check, so replaying the same change is a no-op.
- Work is pulled toward what the user actually reads, so a capped run spends its budget where it matters instead of fetching in listing order.
- The backlog can grow faster than `cap` drains it when the upstream wiki is busy. You can see that on the status page as a queue that doesn't empty, and it's the main reason full English Wikipedia at scale is still an open problem.
