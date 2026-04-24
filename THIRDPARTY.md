# Third-party code and references

This project builds on several open-source projects. License compatibility
notes below; detailed per-site attribution lives in the source files.

## libpc2

- **Upstream**: <https://github.com/toresbe/libpc2>
- **Author**: Tore Sinding Bekkedal
- **License**: GPL-3.0
- **Compatibility**: Our code is GPL-3.0-or-later — compatible.

The parts in `services/masterlink.py` that send messages on the masterlink
bus, in order to make B&O links work, is substantially a derivative work of libpc2.
The file header lists each function that was ported, with references back to the
specific libpc2 source file and function. The ported parts cover
MasterLink telegram serialisation, and the audio-master role handlers
(MASTER_PRESENT reply, GOTO_SOURCE reply, clock broadcast). The ML decode
tables (`_ML_TELEGRAM_TYPES`, `_ML_PAYLOAD_TYPES`, `_ML_NODES`, `_ML_SOURCES`)
publish the same facts libpc2 tabulates in `masterlink/telegram.hpp` and
`masterlink/masterlink.hpp`; most values are also in B&O's MLGW02 spec.

## Beolyd5

- **Upstream**: <https://github.com/larsbaunwall/Beolyd5>
- **Author**: Lars Baunwall
- **License**: Apache 2.0
- **Compatibility**: Apache 2.0 is compatible with GPL-3.0-or-later.

The soft-arc geometry in `web/js/arcs.js` is derived from Beolyd5's arc
rendering. Inline comment in that file points at the upstream source.

## B&O MLGW02 specification

- **Source**: "MLGW Protocol specification, MLGW02, rev 3, 12-Nov-2014"
  (publicly distributed by B&O for third-party integrators).
- The source ID table (§7.2), source activity byte (§7.5), picture format
  byte (§7.6), and Beo4 key-code table (§4.5) are reproduced in
  `services/masterlink.py` as decode dictionaries. These are protocol
  facts, not creative expression.

## Trademarks

"Bang & Olufsen", "BeoSound", "BeoRemote", and "MasterLink" are trademarks
of Bang & Olufsen A/S. This project is not affiliated with or endorsed by
Bang & Olufsen.
