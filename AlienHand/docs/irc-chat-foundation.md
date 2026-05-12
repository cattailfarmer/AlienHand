# IRC Chat Foundation

This note captures the agreed stage-setting for AlienHand chat integration before any implementation work begins.

The SOP specification for this design is captured in `irc-chat-platform.sop`.

The work-to-requirement justification ledger is captured in `work-justification.sop`.

## Goals

- Run an IRC server whenever AlienHand is running.
- Represent each connected AI as its own IRC client connection.
- Use IRC as the live transport layer, not the source of truth.
- Persist durable chat history in JSONL.
- Replay channel history in full, in chunks, when context needs to be reconstructed.
- Allow users to request history through an explicit command.
- Keep each AlienHand app isolated with its own chat server instance when needed.

## Topology

- Each AlienHand app may own a dedicated IRC server instance.
- The IRC server for a given app lives inside that app's lifecycle and starts with it.
- Chat channels are identified by lowercase 32-character UUID hex values.
- IRC channel targets are `#` plus the 32-character channel UUID hex value.
- Channel topics store the human-readable conversation description.
- Channel identity is the UUID, not the topic text.

## Persistence Model

Durable history is append-only JSONL, scoped per channel.

Recommended record fields:

- `ts`
- `channel`
- `sender`
- `sender_type`
- `event_type`
- `content`
- `message_id`
- `correlation_id`
- `reply_to`
- `metadata`

Recommended event types:

- `message`
- `action`
- `join`
- `part`
- `nick_change`
- `topic_change`
- `history_request`
- `system_note`
- `ai_observation`
- `ai_response`

## Replay Model

- Replay full channel history in chunks.
- Back-fill from recent context first, then older messages.
- Stop only when the requested window is satisfied or when a hard context limit is reached.
- Keep replay deterministic by sourcing from append-only logs.
- Treat replay as a data-loading operation, not a human-facing transcript dump.

## Private Messages

- Private messages are treated as ephemeral for now.
- They may be held in live session memory if an AI needs them for the active context window.
- They should be disposed of when the user disconnects or when the session expires.
- They are not part of the durable per-channel JSONL archive unless explicitly promoted later.

## AI Identity

- Every AI agent gets its own IRC client connection.
- Each AI connection should have a stable identity.
- AI message attribution must remain separate from user attribution.
- The chat layer should be able to distinguish live observation from generated response.

## User History Requests

- History access is command-driven.
- The command syntax is not finalized yet.
- The request should become a structured event so replay and auditing can recognize it.
- History delivery should come from the durable log, not from IRC state alone.

## Operational Notes

- IRC state is ephemeral coordination.
- JSONL is the canonical conversation archive.
- Payload files are the canonical rich message bodies.
- Per-app isolation prevents context bleed between AlienHand apps.
- Channel topic changes must not alter log identity.
- The server should keep enough metadata to reconstruct channel state for the AI.

## Open Decisions

- Final command syntax for history requests.
- Exact chunk size and token budgeting for replay.
- Whether to keep a short-lived cache for PM context beyond the live session.
- How much non-message metadata should be exposed back to users during history replay.

## Current Preference

- Durable per-channel history: yes.
- Ephemeral PM context for live AI use: yes.
- Per-app dedicated IRC server: yes.
- Channel UUID hex values as canonical identifiers: yes.
- Channel topics as human-readable labels: yes.

## Current Protocol Direction

- IRC lines carry compact message envelopes only.
- The envelope carries app ID, 32-character channel UUID hex, message UUID, nick, and timestamp with milliseconds.
- Rich message content is resolved from a payload resolver by message UUID.
- Durable payload files remain readable directly for cold replay if the resolver is unavailable.
- The client renders the fetched payload as chat bubbles, code frames, image frames, or linked media frames.
- IRC remains the delivery and identity layer, while payload files and JSONL history carry the durable conversation data.

## Commit Order

For one message, the durability order is:

1. Allocate the message UUID.
2. Write the payload object.
3. Confirm payload durability for the prototype scope.
4. Append the channel JSONL history event.
5. Publish the IRC envelope.

If the payload write or history append fails, IRC publication is blocked. If IRC publication succeeds but payload lookup fails, the client renders a placeholder or explicit `payload_error`.

## First Truth Test

The first implementation proof should be one app, one local Ergo server, one UUID channel, one user client, one AI client, one payload store/resolver, and one JSONL history file.

The prototype succeeds only if it can publish an `AHIRC/1` envelope, resolve the payload by UUID, append history, restart without live IRC memory, and cold-replay the channel from JSONL plus payload files.

## Implemented Prototype Core

The current runtime slice implements the durable substrate core in `alienhand_ai.chat_platform`.

It verifies compact `AH1` envelope round-trip, 32-character channel UUID hex normalization, payload write-before-history-before-publication ordering, JSONL channel append, cold replay from disk, explicit `payload_error` records for missing payloads, socket-level IRC `JOIN`/`PRIVMSG` publication against a fake server, real local Ergo startup/publication/shutdown through `chat-ergo-proof`, AlienHand-owned service lifecycle startup/publication/shutdown through `chat-app-lifecycle-proof`, minimal user IRC client receive plus payload resolution through `chat-user-client-proof`, and chunked payload replay plus payload-backed `history_request` recording through `chat-history-proof`.

The remaining truth-test work is to adapt thelounge rendering around resolved payload objects and settle the final user command syntax for history requests.
