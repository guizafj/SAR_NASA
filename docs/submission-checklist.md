# Submission checklist — Three Cities, One Pump

Freeze **Sunday 16:00**. A tick means a command printed the stated result on the submission commit.
`[owner to fill]` = unverifiable today = blocker, not a pass. Owners (flujo §4): **Rol 1 pozo** ·
**Rol 2 fradiaz** · **Rol 3 yuls**.

## 1. Deliverables

- [ ] **Repo public and importable** — `origin` `git@github.com:neoyuls/SAR_NASA.git`; visibility `[owner to fill]`. **Rol 1 pozo**.
- [ ] **README** — `README.md` §1 what it is, §2 scope + cut lines, §11 done, §13 limitations. **Rol 3 yuls**.
- [ ] **Demo (URL or local run)** — `[owner to fill]` for the URL. The local run is **verified**:
      `./scripts/python.sh -m streamlit run app.py` serves the committed mock (`/_stcore/health` → 200,
      `/` → 200) with no NASA credentials. What is still missing is the deployed URL — `deploy/Dockerfile`
      is in the tree but this machine has no `docker` binary, so the image build is unverified.
      **Rol 2 fradiaz** for the URL, **Rol 1 pozo** for the deploy.
      If the app is not runnable when recording, the backup plan is [docs/demo-fallback.md](demo-fallback.md).
- [ ] **Video** 20 s problem / 90 s demo / 30 s architecture+impact — script `docs/video-script.md`;
      recording `[owner to fill]`. **Rol 3 yuls**.
- [ ] **Provenance evidence** — `meta.json` (`provenance`, `provenance_id`), `docs/data-source-decision.md`,
      `data/processed/_availability/*.json`. **Rol 3 yuls** generates, **Rol 1 pozo** gates, **Rol 2** badges.
- [ ] **Limitations statement** — `README.md §13`, per-case `meta.limitations`, Málaga's detection limit (**Rol 3 yuls** writes, **Rol 2 fradiaz** surfaces).

## 2. Hard gates (on the commit you submit, not a dirty tree)

- [ ] `./scripts/check_phase_a.sh` → `PHASE A/B: PASS`, exit 0 (self-check + the artefact suites + file gate + handoff).
- [ ] `./scripts/preflight.sh` → `VERDICT: READY`, exit 0 (the seven hard gates in one run: contract
      self-check, pytest suite, artefact gate, phase A/B handoff, clone reproduction, figures
      regenerate, demo numbers; ~5 min). It keeps going after a failed gate and prints a per-gate
      table, so a red verdict names what to fix — fix it, do not explain it.
      **Gate 5 also runs the filesystem-only doc guards inside the cloned HEAD**
      (`test_docs_commands`, `test_docs_links`, `test_readme_numbers`, `test_pipeline_readme_numbers`),
      so a committed document that breaks a guard fails the gate and is named by file:line: a green
      working tree is not enough, the *commit* must be green. `--fast` skips only the full pytest
      suite; the clone gate runs in both modes.
      A `note: tree is dirty` or `note: committed artefacts differ from your tree` line means READY
      is **not** the clean-tree reproduction this section asks for.
- [ ] `./scripts/python.sh -m pytest tests -q` → exit 0, no `F`/`E`. `pytest.ini` already adds `-q`,
      so that command is effectively `-qq` and prints **no counts line at all** — judge by the exit
      code, not by a summary. Drop the `-q` (`./scripts/python.sh -m pytest tests`) to see counts.
      Do not quote a pass count here: it moves with every guard added.
- [ ] `./scripts/python.sh -m src.domain.contracts` → `contract fingerprint: <the 64 hex it prints>` = the
      hash in the `docs/data-contract-v1.md` header. The 1.1 bump moves it, so read the printed line
      instead of a remembered one. The retired v1.0 digest was `049376b0…678ce1e` <!-- fingerprint-historical: retired v1.0 digest, kept so the bump is visible; it does not describe this tree -->.
- [ ] **Contract 1.1 carries Rol 1 + Rol 2 sign-off.** The contract's own rule (all three roles agree
      to a `SCHEMA_VERSION` bump) makes the 1.1 adoption on `feat/contract-1.1` a proposal until they
      agree: a green artefact gate is not approval (O-05, 2026-11-15).
- [ ] `git status --porcelain` empty, then clean-tree reproduction (no `.git`, `.venv`, `data/raw`):
      ```bash
      rm -rf /tmp/freeze && mkdir -p /tmp/freeze/repo
      git archive HEAD | tar -x -C /tmp/freeze/repo
      ln -s "$PWD/.venv" /tmp/freeze/repo/.venv
      cd /tmp/freeze/repo && ./scripts/check_phase_a.sh && ./scripts/python.sh -m pytest tests -q
      ```
      → both exit 0. A dirty tree means the commit is not what you tested.
- [ ] Figures newer than the artefacts: `newest=$(ls -t data/processed/*/meta.json | head -1); find
      docs/figures -name '*.png' ! -newer "$newest"` → no output for `velocity_shared_scale.png`,
      `timeseries_three_cities.png`, `coherence.png` (`./scripts/python.sh scripts/make_figures.py`).
- [ ] `data/processed` < 15 MB: `./scripts/python.sh pipeline/validate_artifacts.py --processed
      data/processed --strict-size-mb 15` → `total size: 1.458 MB / 15 MB`, `RESULT: PASS (exit 0)`.
      (The size changes whenever the mock or the AOIs change — re-read it, never quote a remembered
      figure. After the AOI refresh from `config/aoi/*.geojson` it is 1.458 MB, not the earlier 1.607.)
- [ ] `provenance.source` matches `product` on every layer (symmetric rule, `check_phase_a.sh` step 4/4):
      `jq -r '.product + " | " + .provenance.source' data/processed/*/meta.json` → `mock` with a source
      saying mock, a real product with nothing mock. Editing the gate, not the artefact, is forbidden.

## 3. Never cut

- [ ] `provenance` on every artefact and layer (README §2: the only unconditional item).
- [ ] The null result **with its detection limit** — `detection_limit_mm_yr = 1.50` beside median
      −1.36 mm/yr for Málaga; the bound is the result (`docs/data-contract-v1.md` §2.6).
- [ ] The **mock badge** while all three cases are `product=mock` — `scripts/demo_numbers.py` prints
      `!! product == 'mock' for 3/3 case(s)`; badge on screen for the whole demo.
- [ ] The **coverage caveat**: intersecting ≠ covering; < 50 % of AOI H3 cells ⇒ no city-wide claim.

## 4. Cut order if short on time (README §2, in order)

1. [ ] Change-event pins — no events artefact in contract v1.1; post-MVP item 2.
2. [ ] Third city — keep `mexico-city` primary + `malaga` control (`config/study_cases.yaml`, one line).
3. [ ] Everything except Málaga via EGMS — EGMS is Málaga-only, **manual portal download**
       (`egms.land.copernicus.eu`), because Mexico City and Jakarta are non-EU and ineligible
       (`docs/data-source-decision.md` §Options, README §5). This cut deletes the three-city thesis — say it.

## 5. Freeze rule (flujo §7 Fase 5)

- [ ] **May still change after 16:00:** critical fixes, deployment errors, data problems, small visual
      tweaks — one commit each, reviewed by the file's owner.
- [ ] **May not change:** new features, architecture (PMTiles/MapLibre, FastAPI, PostGIS — README §2),
      any contract field/type/enum/threshold (`SCHEMA_VERSION` bump + `CHANGELOG` + three-role review,
      `docs/data-contract-v1.md`), or a product swap without the `provenance` change in that commit.

## 6. Per-role final duties (README §11 definition of done)

- **Rol 1 pozo:** [ ] loaders + validation reject a malformed artefact before the UI sees it; [ ] filters
  and metrics computed once, server-side; [ ] CI reproduces artefacts from a clean checkout, public URL
  up; [ ] `main` runnable.
- **Rol 2 fradiaz:** [ ] five views reachable; [ ] the app starts with **no NASA credentials**; [ ] URL
  restores the exact state; [ ] units and sources always visible, keyboard-navigable, phone-ready.
- **Rol 3 yuls:** [ ] `meta.json` validates for all published cases; [ ] every artefact < 15 MB and
  carries `provenance` + `revisit_days`; [ ] limitations documented, Málaga with its detection limit;
  [ ] video numbers re-derived from `demo_numbers.py` after the last artefact regeneration.

## 7. Known unverified — say these before a judge finds them

- [ ] **No real granule has ever been processed.** All three cases are `product=mock`, the real path is
      tested only on synthetic granules (README §11), and download exits 3 without `EARTHDATA_USER` /
      `EARTHDATA_PASS` + ASF approval (`docs/data-source-decision.md` §Still open).
- [ ] **NISAR urban coverage is unknown.** GUNW is provisional and cryosphere-derived; SME2 excludes
      urban areas, so city-wide displacement is unproven (README §5, §12, §13 limitation 4).
- [ ] **LOS sign convention is unvalidated.** `docs/nisar-gunw-layout.md` is verified against
      documentation, "not yet against a real 2026 granule"; `meta.provenance.method` keeps the
      "NOT yet validated against GNSS" sentence until runbook §7 flips a real median negative.
- [ ] **Season was not separable** over the old 4.5-month window, which is why `seasonal_amp_mm` was
      withheld (`meta.limitations` pre-1.1). Contract **v1.1** publishes the fitted amplitude, ratio and
      regime because the window now spans 2 years, where the same identifiability gates pass
      (O-05, 2026-11-15) — say that, and do not present the fitted seasonal term as a measured cycle.
