"""Mock artefact generator internals (Rol 3).

`pipeline/generate_mock.py` is the CLI; this package holds the pieces:

    cases.py      config/study_cases.yaml -> frozen CaseSpec objects + the date grid
    geography.py  compatibility re-export of `pipeline/context.py` (the case geography: real OSM
                  features by default, with `SYNTHETIC` as the declared fallback)
    model.py      H3 cells, velocity field, coherence, land use, cumulative series, fitted trend
    artifacts.py  deterministic writers for the four frozen artefacts
    checks.py     post-write gates: contracts.validate_all + the assertions this task demands

Nothing here is contract: the frozen schema lives only in `src/domain/contracts.py`.
"""
