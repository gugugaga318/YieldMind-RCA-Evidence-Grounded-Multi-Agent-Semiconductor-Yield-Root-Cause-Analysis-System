# Evaluation V2 Human Review Packet

> Synthetic benchmark only. This packet helps a human reviewer compare labels; it does not approve them.

## Review instructions

For each Retrieval group, confirm that relevance 3 is the best answer and relevance 0 candidates are plausible but wrong. For No-answer groups, confirm that every candidate is related enough to be tempting but none answers the request. For each RCA scenario, independently review causal truth, operational Evidence, and impact scope. Record decisions only in the two versioned review JSON files.

## Retrieval qrel groups

### Q_V2_IF_V2_001_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_01_SRC first showed directional micro-scratch bands, localized copper residue after Copper planarization; scratch density measured 0.82 count/mm2. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_001` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_002` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_003` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_005` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

### Q_V2_IF_V2_001_GUIDE

- Partition: `calibration`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_01_SRC, engineers observed directional micro-scratch bands, localized copper residue after Copper planarization; scratch density measured 0.82 count/mm2. Which approved containment and verification procedure applies?

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |
| 0 | ACCEPTED | `SOP_V2_003` | Cu CMP | Synthetic containment procedure 006 |
| 0 | ACCEPTED | `SOP_V2_005` | Cu CMP | Synthetic containment procedure 010 |
| 0 | ACCEPTED | `SOP_V2_009` | Cu CMP | Synthetic containment procedure 018 |

Candidate excerpts:

- `SOP_V2_001` (rel=3): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 
- `SOP_V2_003` (rel=0): SYNTHETIC SOP. Trigger pattern: single-sector material loss following head rotation; crescent film profile emerging only after planarization. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sourc
- `SOP_V2_005` (rel=0): SYNTHETIC SOP. Trigger pattern: scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 
- `SOP_V2_009` (rel=0): SYNTHETIC SOP. Trigger pattern: curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 

### Q_V2_IF_V2_002_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_02_SRC first showed edge dishing asymmetry, post-polish thickness crescent after Copper planarization; edge-center thickness delta measured 41.0 nm. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_009` | Cu CMP | Synthetic reviewed event 017: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_002` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_003` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_001` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_005` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_009` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

### Q_V2_IF_V2_002_GUIDE

- Partition: `calibration`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Copper planarization reported edge dishing asymmetry, post-polish thickness crescent, with edge-center thickness delta measured 41.0 nm. Retrieve an engineering note that explains how to separate detection location from causal attribution.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_002` | Electroplating | Synthetic causal-scope engineering note 004 |
| 0 | ACCEPTED | `NOTE_V2_008` | Electroplating | Synthetic causal-scope engineering note 016 |
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |

Candidate excerpts:

- `NOTE_V2_002` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish can be detected at Copper planarization without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final 
- `NOTE_V2_008` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing radially increasing recessed topography after planarization; pre-polish metal profile leaning across the wafer can be detected at Copper planarization without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed examp
- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib

### Q_V2_IF_V2_003_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_03_SRC first showed one-sided over-polish arc, azimuthal thickness crescent after Copper planarization; within-wafer removal asymmetry measured 34.0 nm. Find a reviewed historical case with a comparable evidence pattern. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_009` | Cu CMP | Synthetic reviewed event 017: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_003` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_002` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_001` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_005` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_009` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

### Q_V2_IF_V2_003_GUIDE

- Partition: `calibration`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_03_SRC, engineers observed one-sided over-polish arc, azimuthal thickness crescent after Copper planarization; within-wafer removal asymmetry measured 34.0 nm. Which approved containment and verification procedure applies? Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_003` | Cu CMP | Synthetic containment procedure 006 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |
| 0 | ACCEPTED | `SOP_V2_005` | Cu CMP | Synthetic containment procedure 010 |
| 0 | ACCEPTED | `SOP_V2_009` | Cu CMP | Synthetic containment procedure 018 |

Candidate excerpts:

- `SOP_V2_003` (rel=3): SYNTHETIC SOP. Trigger pattern: single-sector material loss following head rotation; crescent film profile emerging only after planarization. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sourc
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 
- `SOP_V2_005` (rel=0): SYNTHETIC SOP. Trigger pattern: scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 
- `SOP_V2_009` (rel=0): SYNTHETIC SOP. Trigger pattern: curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 

### Q_V2_IF_V2_004_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_04_SRC first showed apparent removal-rate step, stable electrical distribution after Inline measurement; reported post-planarization film thickness measured 84.0 nm. Find a reviewed historical case with a comparable evidence pattern. The operator note may contain an incorrect module hint.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 2 | ACCEPTED | `RCA_V2_007` | CVD | Synthetic reviewed event 013: CVD signal |
| 2 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |
| 0 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |
| 0 | ACCEPTED | `RCA_V2_011` | Lithography | Synthetic reviewed event 021: WAT signal |

Candidate excerpts:

- `RCA_V2_004` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_007` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. The signal was detected at Dielectric deposition, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment 
- `RCA_V2_003` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_006` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_010` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v
- `RCA_V2_011` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.

### Q_V2_IF_V2_004_GUIDE

- Partition: `calibration`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Inline measurement reported apparent removal-rate step, stable electrical distribution, with reported post-planarization film thickness measured 84.0 nm. Retrieve an engineering note that explains how to separate detection location from causal attribution. The operator note may contain an incorrect module hint.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_012` | CVD | Synthetic causal-scope engineering note 024 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |
| 0 | ACCEPTED | `NOTE_V2_010` | Ion Implant | Synthetic causal-scope engineering note 020 |

Candidate excerpts:

- `NOTE_V2_004` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_012` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing alternating wafer positions with reduced dielectric deposition; subsequent planarization controls showing no rate loss can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib
- `NOTE_V2_010` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing upper-tail transistor turn-on displacement; interconnect control structures remaining nominal can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the fina

### Q_V2_IF_V2_005_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_05_SRC first showed center residue islands, normal endpoint duration after Copper planarization; residue area ratio measured 3.8 percent. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_005` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_001` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_002` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_003` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T

### Q_V2_IF_V2_005_GUIDE

- Partition: `calibration`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_05_SRC, engineers observed center residue islands, normal endpoint duration after Copper planarization; residue area ratio measured 3.8 percent. Which approved containment and verification procedure applies?

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_005` | Cu CMP | Synthetic containment procedure 010 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |
| 0 | ACCEPTED | `SOP_V2_003` | Cu CMP | Synthetic containment procedure 006 |
| 0 | ACCEPTED | `SOP_V2_009` | Cu CMP | Synthetic containment procedure 018 |

Candidate excerpts:

- `SOP_V2_005` (rel=3): SYNTHETIC SOP. Trigger pattern: scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 
- `SOP_V2_003` (rel=0): SYNTHETIC SOP. Trigger pattern: single-sector material loss following head rotation; crescent film profile emerging only after planarization. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sourc
- `SOP_V2_009` (rel=0): SYNTHETIC SOP. Trigger pattern: curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 

### Q_V2_IF_V2_006_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_06_SRC first showed isolated sidewall roughness, no chamber recurrence after Pattern transfer; roughness index measured 1.7 a.u. Find a reviewed historical case with a comparable evidence pattern. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |
| 1 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 0 | ACCEPTED | `RCA_V2_007` | CVD | Synthetic reviewed event 013: CVD signal |
| 0 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |

Candidate excerpts:

- `RCA_V2_006` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_004` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_007` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. The signal was detected at Dielectric deposition, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment 
- `RCA_V2_010` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v

### Q_V2_IF_V2_006_GUIDE

- Partition: `calibration`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Pattern transfer reported isolated sidewall roughness, no chamber recurrence, with roughness index measured 1.7 a.u. Retrieve an engineering note that explains how to separate detection location from causal attribution. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_010` | Ion Implant | Synthetic causal-scope engineering note 020 |
| 0 | ACCEPTED | `NOTE_V2_012` | CVD | Synthetic causal-scope engineering note 024 |

Candidate excerpts:

- `NOTE_V2_006` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib
- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_010` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing upper-tail transistor turn-on displacement; interconnect control structures remaining nominal can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the fina
- `NOTE_V2_012` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing alternating wafer positions with reduced dielectric deposition; subsequent planarization controls showing no rate loss can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed

### Q_V2_IF_V2_007_RCA

- Partition: `calibration`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_07_SRC first showed true deposited-film thinning, capacitance monitor shift after Dielectric deposition; deposited film thickness loss measured 18.0 nm. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_007` | CVD | Synthetic reviewed event 013: CVD signal |
| 2 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 0 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |
| 1 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |
| 0 | ACCEPTED | `RCA_V2_011` | Lithography | Synthetic reviewed event 021: WAT signal |

Candidate excerpts:

- `RCA_V2_007` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. The signal was detected at Dielectric deposition, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment 
- `RCA_V2_004` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_006` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_010` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v
- `RCA_V2_011` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.

### Q_V2_IF_V2_007_GUIDE

- Partition: `calibration`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_07_SRC, engineers observed true deposited-film thinning, capacitance monitor shift after Dielectric deposition; deposited film thickness loss measured 18.0 nm. Which approved containment and verification procedure applies?

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_007` | CVD | Synthetic containment procedure 014 |
| 0 | ACCEPTED | `SOP_V2_011` | Lithography | Synthetic containment procedure 022 |
| 0 | ACCEPTED | `SOP_V2_013` | Unresolved | Synthetic containment procedure 026 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |

Candidate excerpts:

- `SOP_V2_007` (rel=3): SYNTHETIC SOP. Trigger pattern: repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals;
- `SOP_V2_011` (rel=0): SYNTHETIC SOP. Trigger pattern: meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources;
- `SOP_V2_013` (rel=0): SYNTHETIC SOP. Trigger pattern: brief planarization efficiency reduction without recurrence; later production material remaining inside baseline. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable s
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 

### Q_V2_IF_V2_008_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_08_SRC first showed radial erosion growth, post-polish thickness tilt after Copper planarization; erosion range measured 36.0 nm. Find a reviewed historical case with a comparable evidence pattern. The operator note may contain an incorrect module hint.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_008` | Electroplating | Synthetic reviewed event 015: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_009` | Cu CMP | Synthetic reviewed event 017: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_008` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with radially increasing recessed topography after planarization; pre-polish metal profile leaning across the wafer. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.;
- `RCA_V2_002` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_003` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_001` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_005` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_009` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

### Q_V2_IF_V2_008_GUIDE

- Partition: `test`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Copper planarization reported radial erosion growth, post-polish thickness tilt, with erosion range measured 36.0 nm. Retrieve an engineering note that explains how to separate detection location from causal attribution. The operator note may contain an incorrect module hint.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_008` | Electroplating | Synthetic causal-scope engineering note 016 |
| 0 | ACCEPTED | `NOTE_V2_002` | Electroplating | Synthetic causal-scope engineering note 004 |
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |

Candidate excerpts:

- `NOTE_V2_008` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing radially increasing recessed topography after planarization; pre-polish metal profile leaning across the wafer can be detected at Copper planarization without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed examp
- `NOTE_V2_002` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish can be detected at Copper planarization without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final 
- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib

### Q_V2_IF_V2_009_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_09_SRC first showed repeating arc scratches, stable scratch orientation after Copper planarization; arc defect count measured 14.0 count/wafer. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_009` | Cu CMP | Synthetic reviewed event 017: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 1 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_008` | Electroplating | Synthetic reviewed event 015: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_009` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_001` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_003` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_002` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_005` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_008` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with radially increasing recessed topography after planarization; pre-polish metal profile leaning across the wafer. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.;

### Q_V2_IF_V2_009_GUIDE

- Partition: `test`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_09_SRC, engineers observed repeating arc scratches, stable scratch orientation after Copper planarization; arc defect count measured 14.0 count/wafer. Which approved containment and verification procedure applies?

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_009` | Cu CMP | Synthetic containment procedure 018 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |
| 0 | ACCEPTED | `SOP_V2_003` | Cu CMP | Synthetic containment procedure 006 |
| 0 | ACCEPTED | `SOP_V2_005` | Cu CMP | Synthetic containment procedure 010 |

Candidate excerpts:

- `SOP_V2_009` (rel=3): SYNTHETIC SOP. Trigger pattern: curved surface marks recurring with rinse exposure; defect orientation tied to chamber rotation geometry. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 
- `SOP_V2_003` (rel=0): SYNTHETIC SOP. Trigger pattern: single-sector material loss following head rotation; crescent film profile emerging only after planarization. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sourc
- `SOP_V2_005` (rel=0): SYNTHETIC SOP. Trigger pattern: scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; 

### Q_V2_IF_V2_010_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_10_SRC first showed threshold-voltage tail shift, normal metal resistance after Electrical parametric test; Vt p99 shift measured 47.0 mV. Find a reviewed historical case with a comparable evidence pattern. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |
| 2 | ACCEPTED | `RCA_V2_011` | Lithography | Synthetic reviewed event 021: WAT signal |
| 1 | ACCEPTED | `RCA_V2_014` | WAT | Synthetic reviewed event 027: WAT signal |
| 0 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 0 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |

Candidate excerpts:

- `RCA_V2_010` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v
- `RCA_V2_011` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.
- `RCA_V2_014` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with intermittent high-ohmic probe readings; excursions repeating with measurement-site sequence. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_004` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_006` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s

### Q_V2_IF_V2_010_GUIDE

- Partition: `test`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Electrical parametric test reported threshold-voltage tail shift, normal metal resistance, with Vt p99 shift measured 47.0 mV. Retrieve an engineering note that explains how to separate detection location from causal attribution. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_010` | Ion Implant | Synthetic causal-scope engineering note 020 |
| 0 | ACCEPTED | `NOTE_V2_014` | WAT | Synthetic causal-scope engineering note 028 |
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |

Candidate excerpts:

- `NOTE_V2_010` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing upper-tail transistor turn-on displacement; interconnect control structures remaining nominal can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the fina
- `NOTE_V2_014` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing intermittent high-ohmic probe readings; excursions repeating with measurement-site sequence can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final 
- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib

### Q_V2_IF_V2_011_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_11_SRC first showed serpentine leakage clusters, die-row periodicity after Electrical parametric test; leakage fail rate measured 6.4 percent. Find a reviewed historical case with a comparable evidence pattern. The operator note may contain an incorrect module hint.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_011` | Lithography | Synthetic reviewed event 021: WAT signal |
| 2 | ACCEPTED | `RCA_V2_014` | WAT | Synthetic reviewed event 027: WAT signal |
| 1 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |
| 0 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 0 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |

Candidate excerpts:

- `RCA_V2_011` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.
- `RCA_V2_014` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with intermittent high-ohmic probe readings; excursions repeating with measurement-site sequence. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_010` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v
- `RCA_V2_004` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_006` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s

### Q_V2_IF_V2_011_GUIDE

- Partition: `test`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_11_SRC, engineers observed serpentine leakage clusters, die-row periodicity after Electrical parametric test; leakage fail rate measured 6.4 percent. Which approved containment and verification procedure applies? The operator note may contain an incorrect module hint.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_011` | Lithography | Synthetic containment procedure 022 |
| 0 | ACCEPTED | `SOP_V2_007` | CVD | Synthetic containment procedure 014 |
| 0 | ACCEPTED | `SOP_V2_013` | Unresolved | Synthetic containment procedure 026 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |

Candidate excerpts:

- `SOP_V2_011` (rel=3): SYNTHETIC SOP. Trigger pattern: meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources;
- `SOP_V2_007` (rel=0): SYNTHETIC SOP. Trigger pattern: repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals;
- `SOP_V2_013` (rel=0): SYNTHETIC SOP. Trigger pattern: brief planarization efficiency reduction without recurrence; later production material remaining inside baseline. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable s
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 

### Q_V2_IF_V2_012_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_12_SRC first showed odd-slot film thinning, stable downstream polish rate after Inline measurement; film thickness delta measured -28.0 nm. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_012` | CVD | Synthetic reviewed event 023: Metrology signal |
| 2 | ACCEPTED | `RCA_V2_007` | CVD | Synthetic reviewed event 013: CVD signal |
| 2 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 0 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |
| 0 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |
| 0 | ACCEPTED | `RCA_V2_011` | Lithography | Synthetic reviewed event 021: WAT signal |

Candidate excerpts:

- `RCA_V2_012` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with alternating wafer positions with reduced dielectric deposition; subsequent planarization controls showing no rate loss. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are avail
- `RCA_V2_007` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. The signal was detected at Dielectric deposition, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment 
- `RCA_V2_004` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_006` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_010` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v
- `RCA_V2_011` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.

### Q_V2_IF_V2_012_GUIDE

- Partition: `test`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Inline measurement reported odd-slot film thinning, stable downstream polish rate, with film thickness delta measured -28.0 nm. Retrieve an engineering note that explains how to separate detection location from causal attribution.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_012` | CVD | Synthetic causal-scope engineering note 024 |
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |
| 0 | ACCEPTED | `NOTE_V2_010` | Ion Implant | Synthetic causal-scope engineering note 020 |

Candidate excerpts:

- `NOTE_V2_012` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing alternating wafer positions with reduced dielectric deposition; subsequent planarization controls showing no rate loss can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed
- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib
- `NOTE_V2_010` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing upper-tail transistor turn-on displacement; interconnect control structures remaining nominal can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the fina

### Q_V2_IF_V2_013_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_13_SRC first showed short-lived removal-rate dip, normal subsequent lots after Copper planarization; endpoint extension measured 9.0 s. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_013` | Unresolved | Synthetic reviewed event 025: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |
| 2 | ACCEPTED | `RCA_V2_006` | Unresolved | Synthetic reviewed event 011: Dry Etch signal |
| 1 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |
| 0 | ACCEPTED | `RCA_V2_007` | CVD | Synthetic reviewed event 013: CVD signal |
| 0 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |

Candidate excerpts:

- `RCA_V2_013` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with brief planarization efficiency reduction without recurrence; later production material remaining inside baseline. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available
- `RCA_V2_001` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_005` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_006` (rel=2): SYNTHETIC RCA CASE. A prior production-like event presented with single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs. The signal was detected at Pattern transfer, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_004` (rel=1): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u
- `RCA_V2_007` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. The signal was detected at Dielectric deposition, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment 
- `RCA_V2_010` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v

### Q_V2_IF_V2_013_GUIDE

- Partition: `test`
- Requested type: `SOP`
- Expected No-answer: `false`
- Query: During review of LOT_V2_13_SRC, engineers observed short-lived removal-rate dip, normal subsequent lots after Copper planarization; endpoint extension measured 9.0 s. Which approved containment and verification procedure applies?

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `SOP_V2_013` | Unresolved | Synthetic containment procedure 026 |
| 0 | ACCEPTED | `SOP_V2_007` | CVD | Synthetic containment procedure 014 |
| 0 | ACCEPTED | `SOP_V2_011` | Lithography | Synthetic containment procedure 022 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |

Candidate excerpts:

- `SOP_V2_013` (rel=3): SYNTHETIC SOP. Trigger pattern: brief planarization efficiency reduction without recurrence; later production material remaining inside baseline. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable s
- `SOP_V2_007` (rel=0): SYNTHETIC SOP. Trigger pattern: repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals;
- `SOP_V2_011` (rel=0): SYNTHETIC SOP. Trigger pattern: meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources;
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 

### Q_V2_IF_V2_014_RCA

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `false`
- Query: Lot LOT_V2_14_SRC first showed contact-resistance spikes, site-order recurrence after Electrical parametric test; contact resistance p95 measured 18.0 ohm. Find a reviewed historical case with a comparable evidence pattern.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `RCA_V2_014` | WAT | Synthetic reviewed event 027: WAT signal |
| 0 | ACCEPTED | `RCA_V2_010` | Ion Implant | Synthetic reviewed event 019: WAT signal |
| 0 | ACCEPTED | `RCA_V2_011` | Lithography | Synthetic reviewed event 021: WAT signal |
| 0 | ACCEPTED | `RCA_V2_004` | Metrology | Synthetic reviewed event 007: Metrology signal |

Candidate excerpts:

- `RCA_V2_014` (rel=3): SYNTHETIC RCA CASE. A prior production-like event presented with intermittent high-ohmic probe readings; excursions repeating with measurement-site sequence. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_010` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with upper-tail transistor turn-on displacement; interconnect control structures remaining nominal. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-v
- `RCA_V2_011` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. The signal was detected at Electrical parametric test, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.
- `RCA_V2_004` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift. The signal was detected at Inline measurement, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

### Q_V2_IF_V2_014_GUIDE

- Partition: `test`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `false`
- Query: An investigation at Electrical parametric test reported contact-resistance spikes, site-order recurrence, with contact resistance p95 measured 18.0 ohm. Retrieve an engineering note that explains how to separate detection location from causal attribution.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 3 | ACCEPTED | `NOTE_V2_014` | WAT | Synthetic causal-scope engineering note 028 |
| 0 | ACCEPTED | `NOTE_V2_010` | Ion Implant | Synthetic causal-scope engineering note 020 |
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |

Candidate excerpts:

- `NOTE_V2_014` (rel=3): SYNTHETIC ENGINEERING NOTE. An observation containing intermittent high-ohmic probe readings; excursions repeating with measurement-site sequence can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final 
- `NOTE_V2_010` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing upper-tail transistor turn-on displacement; interconnect control structures remaining nominal can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the fina
- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib

### Q_V2_IF_V2_NA_01_NO_ANSWER

- Partition: `calibration`
- Requested type: `SOP`
- Expected No-answer: `true`
- Query: During review of LOT_V2_NA_01, engineers observed micro-trench corner cracking, low-temperature sidewall haze after Cryogenic trench formation; corner crack density measured 0.9 count/mm2. Which approved containment and verification procedure applies? Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 0 | ACCEPTED | `SOP_V2_007` | CVD | Synthetic containment procedure 014 |
| 0 | ACCEPTED | `SOP_V2_011` | Lithography | Synthetic containment procedure 022 |
| 0 | ACCEPTED | `SOP_V2_013` | Unresolved | Synthetic containment procedure 026 |
| 0 | ACCEPTED | `SOP_V2_001` | Cu CMP | Synthetic containment procedure 002 |

Candidate excerpts:

- `SOP_V2_007` (rel=0): SYNTHETIC SOP. Trigger pattern: repeatable film loss on independent optical measurement; electrical capacitance movement consistent with reduced dielectric thickness. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals;
- `SOP_V2_011` (rel=0): SYNTHETIC SOP. Trigger pattern: meandering electrical short pattern across repeated fields; failure cadence matching exposure-row spacing. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources;
- `SOP_V2_013` (rel=0): SYNTHETIC SOP. Trigger pattern: brief planarization efficiency reduction without recurrence; later production material remaining inside baseline. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable s
- `SOP_V2_001` (rel=0): SYNTHETIC SOP. Trigger pattern: parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. Preserve the source Lot and detection timestamp; compare the actual route; retain candidates from same-step, upstream, shared-resource, and global lanes; verify current-Lot operational signals; record unavailable sources; stop with an 

### Q_V2_IF_V2_NA_02_NO_ANSWER

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `true`
- Query: Lot LOT_V2_NA_02 first showed buried rail reveal voids, localized silicon tearing after Backside power reveal; reveal void fraction measured 2.6 percent. Find a reviewed historical case with a comparable evidence pattern. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 0 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_001` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_002` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_003` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_005` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

### Q_V2_IF_V2_NA_03_NO_ANSWER

- Partition: `calibration`
- Requested type: `ENGINEERING_NOTE`
- Expected No-answer: `true`
- Query: An investigation at Anamorphic overlay control reported field-edge overlay rotation, scan-direction asymmetry, with overlay vector magnitude measured 4.8 nm. Retrieve an engineering note that explains how to separate detection location from causal attribution. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 0 | ACCEPTED | `NOTE_V2_004` | Metrology | Synthetic causal-scope engineering note 008 |
| 0 | ACCEPTED | `NOTE_V2_006` | Unresolved | Synthetic causal-scope engineering note 012 |
| 0 | ACCEPTED | `NOTE_V2_010` | Ion Implant | Synthetic causal-scope engineering note 020 |
| 0 | ACCEPTED | `NOTE_V2_012` | CVD | Synthetic causal-scope engineering note 024 |

Candidate excerpts:

- `NOTE_V2_004` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing abrupt optical film readback displacement; unchanged electrical monitors during the reported process shift can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the
- `NOTE_V2_006` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing single-wafer profile texture excursion; absence of repeated signatures on adjacent chamber runs can be detected at Pattern transfer without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the final attrib
- `NOTE_V2_010` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing upper-tail transistor turn-on displacement; interconnect control structures remaining nominal can be detected at Electrical parametric test without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed example the fina
- `NOTE_V2_012` (rel=0): SYNTHETIC ENGINEERING NOTE. An observation containing alternating wafer positions with reduced dielectric deposition; subsequent planarization controls showing no rate loss can be detected at Inline measurement without originating there. Preserve route distance, configured resource relations, and metrology repeatability as separate features. In this reviewed

### Q_V2_IF_V2_NA_04_NO_ANSWER

- Partition: `test`
- Requested type: `RCA_CASE`
- Expected No-answer: `true`
- Query: Lot LOT_V2_NA_04 first showed interfacial nano-void ring, post-anneal edge separation after Plasma-activated bonding; bond void ratio measured 1.4 percent. Find a reviewed historical case with a comparable evidence pattern. Equipment identity is not available.

| Relevance | Decision | Asset | Causal/asset Module | Title |
|---:|---|---|---|---|
| 0 | ACCEPTED | `RCA_V2_001` | Cu CMP | Synthetic reviewed event 001: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_002` | Electroplating | Synthetic reviewed event 003: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_003` | Cu CMP | Synthetic reviewed event 005: Cu CMP signal |
| 0 | ACCEPTED | `RCA_V2_005` | Cu CMP | Synthetic reviewed event 009: Cu CMP signal |

Candidate excerpts:

- `RCA_V2_001` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with parallel polish tracks following platen motion; incomplete metal clearing in isolated zones. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-visible s
- `RCA_V2_002` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with perimeter recess with a one-sided radial profile; crescent-shaped film-map imbalance after polish. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The user-vis
- `RCA_V2_003` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with single-sector material loss following head rotation; crescent film profile emerging only after planarization. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; T
- `RCA_V2_005` (rel=0): SYNTHETIC RCA CASE. A prior production-like event presented with scattered unremoved metal patches near wafer center; completion timing remaining inside its control band. The signal was detected at Copper planarization, but the investigation kept that location as a ranking hint. Evidence reviewed: Source Lot route and equipment exposure are available.; The u

## RCA scenario groups

### RCA_V2_IF_V2_001

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `same_step` / `same_step`
- Expected status: `supported`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Cu CMP`
- Expected root cause: CMP_CU_11 pad conditioner bearing drag caused intermittent pad glazing
- Expected impact Lots: LOT_V2_01_IMP_01, LOT_V2_01_IMP_02
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_01_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_01_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_01_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_002, RCA_V2_003, RCA_V2_005

### RCA_V2_IF_V2_002

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `upstream` / `upstream_route`
- Expected status: `supported`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Electroplating`
- Expected root cause: PLATE_CU_04 anode contact intermittency created an upstream copper profile skew
- Expected impact Lots: LOT_V2_02_IMP_01, LOT_V2_02_IMP_02, LOT_V2_02_IMP_03
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_02_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_02_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_02_PRE_CMP_PROFILE`: Pre-CMP incoming copper thickness tilt measured 38.0 nm, proving the profile existed before polish.
  - `EV_V2_02_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_001, RCA_V2_005, RCA_V2_009

### RCA_V2_IF_V2_003

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `shared_resource` / `shared_resource`
- Expected status: `supported`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Cu CMP`
- Expected root cause: CMP_CU_13 carrier-head orbit runout caused azimuthal over-polish
- Expected impact Lots: LOT_V2_03_IMP_01, LOT_V2_03_IMP_02
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_03_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_03_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_03_PRE_CMP_NORMAL`: Pre-CMP incoming copper thickness tilt measured 2.0 nm inside specification, weakening the upstream-profile hypothesis.
  - `EV_V2_03_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_001, RCA_V2_005, RCA_V2_009

### RCA_V2_IF_V2_004

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `metrology_artifact` / `global_semantic`
- Expected status: `supported`
- Observed at: `Metrology` / `Inline measurement`
- Expected causal Module: `Metrology`
- Expected root cause: MET_FILM_03 reference-wafer offset produced a false post-CMP thickness shift
- Expected impact Lots: None
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_04_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_04_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_04_REFERENCE_CALIBRATION`: MET_FILM_03 failed its reference-wafer calibration check with a reproducible offset.
  - `EV_V2_04_INDEPENDENT_REMEASURE`: Independent remeasurement on MET_FILM_04 returned 100.0 nm inside specification.
  - `EV_V2_04_PROCESS_FDC_NORMAL`: Current-Lot CVD and Cu CMP operational signals remain normal.
  - `EV_V2_04_WAT_NORMAL`: Current-Lot electrical checks remain normal, supporting a measurement artifact rather than a real process excursion.
  - `EV_V2_04_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_006, RCA_V2_010, RCA_V2_011

### RCA_V2_IF_V2_005

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `conflicting_evidence` / `same_step`
- Expected status: `inconclusive`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Cu CMP`
- Expected root cause: unverified CMP slurry delivery restriction
- Expected impact Lots: None
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_05_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_05_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_05_CAUSAL_SIGNAL`: A transient slurry-pump pressure-drop index exceeded its control limit.
  - `EV_V2_05_CONTRADICTION`: The independent slurry flow meter and CMP endpoint duration remained normal, so the suspected delivery restriction cannot be confirmed.
- Hard-negative hypotheses: RCA_V2_001, RCA_V2_002, RCA_V2_003

### RCA_V2_IF_V2_006

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `insufficient_evidence` / `global_semantic`
- Expected status: `inconclusive`
- Observed at: `Dry Etch` / `Pattern transfer`
- Expected causal Module: `Unresolved`
- Expected root cause: UNRESOLVED because current-lot equipment evidence is missing
- Expected impact Lots: None
- Unavailable sources: Dry Etch FDC feature history
- Evidence under review:
  - `EV_V2_06_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_06_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_06_SAME_CHAMBER_CONTROLS`: Two adjacent control Lots ran on ETCH_METAL_08 without the sidewall defect, showing no chamber-level recurrence while leaving a one-wafer transient open.
- Hard-negative hypotheses: RCA_V2_004, RCA_V2_007, RCA_V2_010

### RCA_V2_IF_V2_007

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `same_step` / `same_step`
- Expected status: `supported`
- Observed at: `CVD` / `Dielectric deposition`
- Expected causal Module: `CVD`
- Expected root cause: CVD_FILM_07 chamber-temperature drift reduced deposition rate
- Expected impact Lots: LOT_V2_07_IMP_01, LOT_V2_07_IMP_02
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_07_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_07_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_07_IMPACT_CORRELATION`: The source Lot and two exposed Lots share CVD_FILM_07 temperature OOC, independently measured film loss, and same-direction electrical movement.
  - `EV_V2_07_RECOVERY_CONTROLS`: Two later control Lots on CVD_FILM_07 have normal temperature, film thickness, and WAT after chamber-temperature recovery.
  - `EV_V2_07_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_006, RCA_V2_010, RCA_V2_011

### RCA_V2_IF_V2_008

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `upstream` / `upstream_route`
- Expected status: `supported`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Electroplating`
- Expected root cause: PLATE_CU_09 agitation controller drift distorted the incoming copper profile
- Expected impact Lots: LOT_V2_08_IMP_01, LOT_V2_08_IMP_02, LOT_V2_08_IMP_03
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_08_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_08_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_08_PRE_CMP_PROFILE`: Pre-CMP incoming copper thickness tilt measured 33.0 nm, proving the profile existed before polish.
  - `EV_V2_08_IMPACT_CORRELATION`: The source Lot and three affected Lots share PLATE_CU_09 agitation OOC, Pre-CMP copper tilt, and post-CMP erosion.
  - `EV_V2_08_RECOVERY_CONTROLS`: Two later control Lots on PLATE_CU_09 have normal agitation, incoming copper profiles, and post-CMP results after controller recovery.
  - `EV_V2_08_DETECTED_STEP_NORMAL`: CMP_CU_21 removal-rate and endpoint signals remain normal on the source Lot.
  - `EV_V2_08_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_001, RCA_V2_005, RCA_V2_009

### RCA_V2_IF_V2_009

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `shared_resource` / `shared_resource`
- Expected status: `supported`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Cu CMP`
- Expected root cause: CMP_CU_22 post-polish rinse chamber particle shedding marked wafers
- Expected impact Lots: LOT_V2_09_IMP_01, LOT_V2_09_IMP_02, LOT_V2_09_IMP_03
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_09_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_09_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_09_IMPACT_CORRELATION`: The source Lot and three affected Lots share CMP_CU_22_CH01 particle OOC and matching arc orientation, curvature, and location distributions.
  - `EV_V2_09_RECOVERY_CONTROLS`: Two later control Lots on CMP_CU_22_CH01 have normal particle indicators and defect counts after rinse-chamber cleaning.
  - `EV_V2_09_DETECTED_STEP_NORMAL`: CMP_CU_22 carrier-head runout and pad-conditioner torque remain normal.
  - `EV_V2_09_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_002, RCA_V2_005, RCA_V2_008

### RCA_V2_IF_V2_010

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `global_cross_module` / `global_semantic`
- Expected status: `supported`
- Observed at: `WAT` / `Electrical parametric test`
- Expected causal Module: `Ion Implant`
- Expected root cause: IMP_WELL_03 dose-integrator drift shifted well implant dose
- Expected impact Lots: LOT_V2_10_IMP_01, LOT_V2_10_IMP_02
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_10_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_10_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_10_IMPACT_CORRELATION`: The source Lot and two affected Lots share IMP_WELL_03 dose-integrator OOC and same-direction Vt shifts.
  - `EV_V2_10_RECOVERY_CONTROLS`: Two later control Lots on IMP_WELL_03 have normal dose integration and Vt after recalibration.
  - `EV_V2_10_INDEPENDENT_WAT_RETEST`: Independent retest on WAT_CELL_09 reproduces the Vt shift on all three affected Lots, excluding a single-tester artifact.
  - `EV_V2_10_ELECTRICAL_CONTROLS_NORMAL`: Metal resistance and reticle-field periodicity controls remain normal.
  - `EV_V2_10_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_014, RCA_V2_004, RCA_V2_006

### RCA_V2_IF_V2_011

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `global_cross_module` / `global_semantic`
- Expected status: `supported`
- Observed at: `WAT` / `Electrical parametric test`
- Expected causal Module: `Lithography`
- Expected root cause: LITHO_SCN_06 reticle haze repeated a line-space bridging signature
- Expected impact Lots: LOT_V2_11_IMP_01, LOT_V2_11_IMP_02
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_11_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_11_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_11_IMPACT_CORRELATION`: The source Lot and two affected Lots share LITHO_SCN_06 reticle-scatter OOC and leakage periodicity aligned to exposure-field spacing.
  - `EV_V2_11_RECOVERY_CONTROLS`: Two later control Lots on LITHO_SCN_06 have normal reticle scatter and leakage after reticle cleaning.
  - `EV_V2_11_INDEPENDENT_WAT_RETEST`: Independent retest on WAT_CELL_10 reproduces the same leakage rate and spatial periodicity on all affected Lots.
  - `EV_V2_11_ELECTRICAL_CONTROLS_NORMAL`: Vt shift, probe contact resistance, and test-sequence correlation remain normal, weakening implant and probe-card hypotheses.
  - `EV_V2_11_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_010, RCA_V2_004, RCA_V2_006

### RCA_V2_IF_V2_012

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `impact_lot_truth` / `upstream_route`
- Expected status: `supported`
- Observed at: `Metrology` / `Inline measurement`
- Expected causal Module: `CVD`
- Expected root cause: CVD_ILD_17 susceptor sensor intermittency reduced odd-slot deposition rate
- Expected impact Lots: LOT_V2_12_IMP_01, LOT_V2_12_IMP_02, LOT_V2_12_IMP_03
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_12_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_12_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_12_IMPACT_CORRELATION`: The source Lot and three affected Lots share CVD_ILD_17 sensor OOC and film loss on odd wafer slots only.
  - `EV_V2_12_RECOVERY_CONTROLS`: Two later control Lots on CVD_ILD_17 have normal FDC and all six wafer slots inside film-thickness specification after sensor replacement.
  - `EV_V2_12_DETECTED_STEP_NORMAL`: Downstream CMP removal-rate and endpoint signals remain normal.
  - `EV_V2_12_INDEPENDENT_METROLOGY`: Independent MET_FILM_12 remeasurement reproduces the odd-slot film-loss pattern while even slots remain normal.
  - `EV_V2_12_CAUSAL_SIGNAL`: Current-Lot operational evidence supports the causal attribution.
- Hard-negative hypotheses: RCA_V2_006, RCA_V2_010, RCA_V2_011

### RCA_V2_IF_V2_013

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `unsupported_data_source` / `shared_resource`
- Expected status: `inconclusive`
- Observed at: `Cu CMP` / `Copper planarization`
- Expected causal Module: `Unresolved`
- Expected root cause: UNRESOLVED after normal pad and equipment checks because chemical batch genealogy is unavailable
- Expected impact Lots: None
- Unavailable sources: chemical batch genealogy
- Evidence under review:
  - `EV_V2_13_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_13_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_13_PRE_CMP_NORMAL`: Pre-CMP incoming film thickness tilt measured 1.0 nm inside specification, weakening the upstream-profile hypothesis.
  - `EV_V2_13_SAME_CHAMBER_CONTROLS`: Two later control Lots on CMP_CU_23 have normal removal and endpoint behavior.
  - `EV_V2_13_DETECTED_STEP_NORMAL`: Pad age, conditioner torque, slurry delivery, carrier pressure, and platen speed remain normal on the source Lot.
  - `EV_V2_13_DETECTED_STEP_EXCURSION`: The source Lot has a real endpoint extension and removal-rate dip on CMP_CU_23; later controls are normal.
  - `EV_V2_13_INDEPENDENT_PROCESS_CONFIRMATION`: Independent post-CMP metrology confirms residual film on the source Lot, excluding a pure endpoint-sensor artifact.
- Hard-negative hypotheses: RCA_V2_004, RCA_V2_007, RCA_V2_010

### RCA_V2_IF_V2_014

- Overall review decision: `ACCEPTED`
- Root-cause review: `ACCEPTED`
- Evidence-chain review: `ACCEPTED`
- Impact-scope review: `ACCEPTED`
- Category / lane: `same_step` / `same_step`
- Expected status: `supported`
- Observed at: `WAT` / `Electrical parametric test`
- Expected causal Module: `WAT`
- Expected root cause: WAT_CELL_12 probe-card contamination caused false resistance spikes
- Expected impact Lots: None
- Unavailable sources: None
- Evidence under review:
  - `EV_V2_14_MES_ROUTE`: Source Lot route and equipment exposure are available.
  - `EV_V2_14_OBSERVATION`: The user-visible symptom and measurement are reproducible.
  - `EV_V2_14_INDEPENDENT_WAT_RETEST`: The retained source wafer passes on independent WAT_CELL_13, excluding a reproducible wafer electrical excursion.
  - `EV_V2_14_EQUIPMENT_INSPECTION`: Optical inspection finds contamination residue on the WAT_CELL_12 probe card.
  - `EV_V2_14_POST_CLEAN_RECOVERY`: After probe-card cleaning and qualification, the retained wafer and two later control Lots pass on WAT_CELL_12 without measurement-sequence repetition.
  - `EV_V2_14_IMPACT_SCOPE_AUDIT`: The qualification-to-cleaning equipment genealogy contains only the source Lot, so no additional impacted Lots are confirmed.
  - `EV_V2_14_CAUSAL_SIGNAL`: WAT_CELL_12 probe-contact repeatability is abnormal on the source Lot; this localizes the signal to the measurement system but does not alone prove probe-card contamination.
- Hard-negative hypotheses: RCA_V2_010, RCA_V2_011, RCA_V2_004

