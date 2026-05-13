# AlienHand thelounge Adapter

This directory contains the first AlienHand-owned client adapter for rendering
payload-backed chat rows in a thelounge-style surface without modifying the
upstream `AlienHand/thelounge` submodule directly.

The submodule currently tracks `https://github.com/thelounge/thelounge.git`.
Direct edits inside that submodule would require a pushed thelounge fork commit
before the parent repository could safely point at it. Until that fork target is
available, this adapter keeps the UI contract inspectable and commit-safe.

## Adapter Contract

- Input is the AlienHand render model produced by `payload_to_render_model`.
- User rows render left.
- AI-agent rows render right.
- Service and payload-error rows render as system rows.
- Code, image, file, and link frames render as explicit frame blocks.
- Rich content remains payload-backed; the IRC line remains only an envelope.

## Integration Target

When the thelounge fork is ready, copy or port:

- `client/components/AlienHandMessage.vue`
- `client/css/alienhand-chat.css`

The intended insertion point is near the existing thelounge message path:

- `client/components/Message.vue`
- `client/components/ParsedMessage.vue`
- `client/components/MessageList.vue`

The first fork integration should detect an AlienHand payload-backed render row
and route it to `AlienHandMessage` instead of displaying the raw `AH1` envelope
as plain IRC text.
