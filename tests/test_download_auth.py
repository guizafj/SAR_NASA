"""Hermetic tests for the Earthdata/ASF download authentication in 02_search_download.py.

Every test replaces the HTTP session with a scripted fake: no sockets, no ASF, no Earthdata
Login. The fake records the request sequence so the ASF → EDL → presigned-S3 redirect chain and
the per-host credential scoping are asserted directly instead of being assumed.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # running pytest from another cwd
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "search_download_auth", ROOT / "pipeline" / "ingest" / "02_search_download.py"
)
assert _SPEC is not None and _SPEC.loader is not None
M = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = M
_SPEC.loader.exec_module(M)

GUNW_ID = (
    "NISAR_L2_PR_GUNW_032_113_D_079_033_4000_SH_"
    "20261008T011329_20261008T011403_20261020T011329_20261020T011403_P05023_N_F_J_001"
)
USER = "fake-earthdata-user"
PASSWORD = "fake-s3cr3t-hunter2-password"  # must never appear in logs/exceptions

ASF_HOST = "https://datapool.asf.alaska.edu"
GRANULE_URL = f"{ASF_HOST}/GUNW/{GUNW_ID}.h5"
EDL_URL = (
    "https://urs.earthdata.nasa.gov/oauth/authorize?client_id=fake-client-id"
    "&response_type=code&redirect_uri=https%3A%2F%2Fdatapool.asf.alaska.edu%2Foauth"
)
SIGNED_URL = (
    f"{ASF_HOST}/GUNW/{GUNW_ID}.h5?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=fake-credential&X-Amz-Signature=deadbeefcafe"
)
SIGNATURE = "deadbeefcafe"


# ---------------------------------------------------------------------------------------------
# Fake HTTP session: replays canned responses in order and records every request
# ---------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._chunks = list(chunks) if chunks is not None else ([body] if body else [])
        self.closed = False
        self.chunk_requests: list[int] = []

    def iter_content(self, chunk_size: int = 1 << 20):
        self.chunk_requests.append(chunk_size)
        for chunk in self._chunks:
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Cookie-carrying session stand-in: `scripted` responses are consumed in call order."""

    def __init__(self, scripted: list[FakeResponse]) -> None:
        self.scripted = list(scripted)
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url, headers=None, stream=False, allow_redirects=True, timeout=None, **_kw):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers or {}),
                "stream": stream,
                "allow_redirects": allow_redirects,
                "timeout": timeout,
            }
        )
        assert self.scripted, f"unexpected extra request to {url}"
        return self.scripted.pop(0)

    @property
    def urls(self) -> list[str]:
        return [call["url"] for call in self.calls]


def _aoi_availability(tmp_path: pathlib.Path, monkeypatch, size_mb: float | None) -> pathlib.Path:
    city = "test-city"
    aois = {
        "cities": {
            city: {"name": "Test City", "bbox": [-1.0, 0.0, 1.0, 2.0], "crs": "EPSG:32630"}
        }
    }
    aois_path = tmp_path / "aois.json"
    aois_path.write_text(json.dumps(aois))
    monkeypatch.setattr(M, "AOIS", aois_path)

    availability = tmp_path / "availability"
    availability.mkdir()
    report = {
        "contract_version": "1.0",
        "city": city,
        "sources": {
            "nisar_gunw": {
                "attempted": True,
                "available": True,
                "n_granules": 1,
                "sample_granules": [
                    {
                        "id": GUNW_ID,
                        "url": GRANULE_URL,
                        "size_mb": size_mb,
                        "start": "2026-10-08T01:13:29Z",
                        "stop": "2026-10-20T01:14:03Z",
                    }
                ],
            }
        },
        "chosen_source": "NISAR_L2_GUNW",
    }
    (availability / f"{city}.json").write_text(json.dumps(report))
    return availability


def _argv(availability: pathlib.Path, raw: pathlib.Path, *extra: str) -> list[str]:
    return [
        "download",
        "--city",
        "test-city",
        "--source",
        "nisar_gunw",
        "--availability",
        str(availability),
        "--raw",
        str(raw),
        *extra,
    ]


def _creds(monkeypatch) -> None:
    monkeypatch.setenv("EARTHDATA_USER", USER)
    monkeypatch.setenv("EARTHDATA_PASS", PASSWORD)
    # A real ~/.netrc must not leak into these tests.
    monkeypatch.setattr(M.pathlib.Path, "home", lambda: pathlib.Path("/nonexistent-home"))


def _install_session(monkeypatch, session: FakeSession) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def fake_build(user: str, pwd: str):
        seen.append((user, pwd))
        return session

    monkeypatch.setattr(M, "build_earthdata_session", fake_build)
    return seen


def _expected_basic_auth() -> str:
    token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    return f"Basic {token}"


# ---------------------------------------------------------------------------------------------
# The happy path: 302 → EDL login/consent → 302 → presigned 200
# ---------------------------------------------------------------------------------------------


def test_redirect_chain_is_followed_and_body_reaches_destination(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    raw = tmp_path / "raw"
    body = b"granule-bytes" * 1000
    session = FakeSession(
        [
            FakeResponse(302, {"Location": EDL_URL}),
            FakeResponse(302, {"Location": SIGNED_URL}),
            FakeResponse(200, {"Content-Type": "application/octet-stream"}, body=body),
        ]
    )
    seen = _install_session(monkeypatch, session)

    rc = M.main(_argv(availability, raw))

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    dest = raw / "test-city" / "nisar_gunw" / f"{GUNW_ID}.h5"
    assert dest.read_bytes() == body
    assert not dest.with_suffix(dest.suffix + ".part").exists()

    # Credentials come from the fail-closed helper, not from thin air.
    assert seen == [(USER, PASSWORD)]

    # ...and provenance keeps the old sha256/url/user behaviour.
    provenance = json.loads((dest.parent / "provenance.json").read_text())
    record = provenance["files"][0]
    assert record["sha256"] == hashlib.sha256(body).hexdigest()
    assert record["bytes"] == len(body)
    assert record["url"] == GRANULE_URL
    assert provenance["earthdata_user"] == USER


def test_request_sequence_and_per_host_basic_auth(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    raw = tmp_path / "raw"
    session = FakeSession(
        [
            FakeResponse(302, {"Location": EDL_URL}),
            FakeResponse(302, {"Location": SIGNED_URL}),
            FakeResponse(200, {"Content-Type": "application/octet-stream"}, body=b"x"),
        ]
    )
    _install_session(monkeypatch, session)

    assert M.main(_argv(availability, raw)) == 0
    capsys.readouterr()

    # Exactly the real chain, in order, each hop fetched without auto-redirects.
    assert session.urls == [GRANULE_URL, EDL_URL, SIGNED_URL]
    assert all(call["allow_redirects"] is False for call in session.calls)
    assert all(call["stream"] is True for call in session.calls)

    # Basic auth on the Earthdata Login host only: never on ASF, never on the presigned URL.
    assert "Authorization" not in session.calls[0]["headers"]
    assert session.calls[1]["headers"]["Authorization"] == _expected_basic_auth()
    assert "Authorization" not in session.calls[2]["headers"]
    # The signature lives in the URL and is never logged.
    assert SIGNATURE not in capsys.readouterr().out


def test_fresh_download_sends_no_range_header(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    session = FakeSession(
        [FakeResponse(200, {"Content-Type": "application/octet-stream"}, body=b"abc")]
    )
    _install_session(monkeypatch, session)

    assert M.main(_argv(availability, tmp_path / "raw")) == 0
    capsys.readouterr()
    assert "Range" not in session.calls[0]["headers"]


# ---------------------------------------------------------------------------------------------
# 401 / 403 diagnosis
# ---------------------------------------------------------------------------------------------


def test_401_exits_nonzero_with_authorised_apps_and_credentials_checklist(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    raw = tmp_path / "raw"
    session = FakeSession(
        [
            FakeResponse(302, {"Location": EDL_URL}),
            FakeResponse(401, {"Content-Type": "text/html"}),
        ]
    )
    _install_session(monkeypatch, session)

    rc = M.main(_argv(availability, raw))

    captured = capsys.readouterr()
    assert rc != 0
    assert rc == M.RC_CREDENTIALS
    assert "Traceback" not in captured.err
    err = captured.err
    # 401 checklist: EULA / Authorized Apps route, plus the credential hint.
    assert "401" in err
    assert "urs.earthdata.nasa.gov" in err
    assert "Applications -> Authorized Apps" in err
    assert "Alaska Satellite Facility Data Access" in err
    assert "EULA" in err
    assert "EARTHDATA_USER" in err and "EARTHDATA_PASS" in err
    assert "~/.netrc" in err
    assert "password" in err.lower()
    # The EDL URL's query (client_id, redirect_uri) is not echoed verbatim.
    assert "client_id" not in err
    assert "response_type" not in err
    # Nothing was downloaded and no provenance was invented.
    assert not (raw / "test-city" / "nisar_gunw" / f"{GUNW_ID}.h5").exists()
    assert not (raw / "test-city" / "nisar_gunw" / "provenance.json").exists()


def test_403_produces_a_distinct_restricted_granule_message(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    session = FakeSession(
        [
            FakeResponse(302, {"Location": EDL_URL}),
            FakeResponse(302, {"Location": SIGNED_URL}),
            FakeResponse(403, {"Content-Type": "text/plain"}),
        ]
    )
    _install_session(monkeypatch, session)

    rc = M.main(_argv(availability, tmp_path / "raw"))

    captured = capsys.readouterr()
    assert rc != 0
    assert rc == M.RC_ACCESS
    assert rc != M.RC_CREDENTIALS
    assert "403" in captured.err
    assert "restricted" in captured.err
    assert "authoris" in captured.err.lower()
    # Forbidden is not "your password is wrong": the 401 checklist must not be recycled.
    assert "wrong password fails" not in captured.err
    assert "Traceback" not in captured.err
    # The presigned query string is redacted, not printed.
    assert SIGNATURE not in captured.err
    assert "?<redacted>" in captured.err


def test_edl_login_page_returned_as_200_is_reported_as_auth_failure(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    """Wrong credentials can surface as 200 + the EDL HTML login form, not as a 401."""
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    raw = tmp_path / "raw"
    session = FakeSession(
        [
            FakeResponse(302, {"Location": EDL_URL}),
            FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, body=b"<form>"),
        ]
    )
    _install_session(monkeypatch, session)

    rc = M.main(_argv(availability, raw))

    captured = capsys.readouterr()
    assert rc == M.RC_CREDENTIALS
    assert "Earthdata Login form" in captured.err
    assert "Authorized Apps" in captured.err
    assert not (raw / "test-city" / "nisar_gunw" / f"{GUNW_ID}.h5").exists()


# ---------------------------------------------------------------------------------------------
# Resumability and the size guard survive the new auth path
# ---------------------------------------------------------------------------------------------


def test_resume_sends_range_and_appends_instead_of_truncating(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    raw = tmp_path / "raw"
    dest = raw / "test-city" / "nisar_gunw" / f"{GUNW_ID}.h5"
    dest.parent.mkdir(parents=True)
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"0123456789")
    response = FakeResponse(206, {"Content-Type": "application/octet-stream"}, body=b"abcdef")
    session = FakeSession([response])
    _install_session(monkeypatch, session)

    rc = M.main(_argv(availability, raw))

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert session.calls[0]["headers"]["Range"] == "bytes=10-"
    assert dest.read_bytes() == b"0123456789abcdef"  # appended, not truncated
    assert not part.exists()
    # Streamed in bounded chunks: a multi-GB granule is never buffered in memory.
    assert response.chunk_requests == [M.CHUNK]


def test_resume_restarts_when_server_ignores_range(tmp_path: pathlib.Path) -> None:
    dest = tmp_path / "granule.h5"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"stale-prefix")
    session = FakeSession([FakeResponse(200, {}, body=b"complete")])

    written = M.download_one(GRANULE_URL, dest, 10_000, USER, PASSWORD, session=session)

    assert session.calls[0]["headers"]["Range"] == "bytes=12-"
    assert written == len(b"complete")
    assert dest.read_bytes() == b"complete"


def test_max_gb_mid_stream_refusal_keeps_part_file(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=None)  # unknown size
    raw = tmp_path / "raw"
    dest = raw / "test-city" / "nisar_gunw" / f"{GUNW_ID}.h5"
    part = dest.with_suffix(dest.suffix + ".part")
    session = FakeSession(
        [
            FakeResponse(
                200,
                {"Content-Type": "application/octet-stream"},
                chunks=[b"a" * 2000, b"b" * 2000, b"c" * 2000],
            )
        ]
    )
    _install_session(monkeypatch, session)

    rc = M.main(_argv(availability, raw, "--max-gb", "0.000005"))  # 5 kB budget

    captured = capsys.readouterr()
    assert rc == M.RC_BUDGET_GUARD
    assert "exceeded --max-gb" in captured.err
    assert part.exists(), "the partial must stay for resumption"
    assert part.stat().st_size >= 5000
    assert not dest.exists()


def test_download_one_raises_on_budget_and_leaves_part(tmp_path: pathlib.Path) -> None:
    dest = tmp_path / "granule.h5"
    part = dest.with_suffix(dest.suffix + ".part")
    session = FakeSession(
        [FakeResponse(200, {}, chunks=[b"x" * 3000, b"y" * 3000])]
    )

    try:
        M.download_one(GRANULE_URL, dest, 4000, USER, PASSWORD, session=session)
    except RuntimeError as exc:
        assert "--max-gb" in str(exc)
        assert PASSWORD not in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("download_one did not enforce the byte guard")

    assert part.exists()
    assert part.stat().st_size >= 4000
    assert not dest.exists()


# ---------------------------------------------------------------------------------------------
# Secrets never escape
# ---------------------------------------------------------------------------------------------


def test_password_never_appears_in_any_log_or_exception(
    tmp_path: pathlib.Path, monkeypatch, capsys
) -> None:
    _creds(monkeypatch)
    availability = _aoi_availability(tmp_path, monkeypatch, size_mb=1)
    raw = tmp_path / "raw"
    session = FakeSession(
        [
            FakeResponse(302, {"Location": EDL_URL}),
            FakeResponse(401, {"Content-Type": "text/html"}),
        ]
    )
    _install_session(monkeypatch, session)

    texts: list[str] = []
    rc = M.main(_argv(availability, raw))
    captured = capsys.readouterr()
    texts += [captured.out, captured.err]

    # Also the exception text and the stray non-auth failure path.
    try:
        M.download_one(
            GRANULE_URL,
            tmp_path / "other.h5",
            1_000_000,
            USER,
            PASSWORD,
            session=FakeSession([FakeResponse(500, {})]),
        )
    except RuntimeError as exc:
        texts.append(str(exc))

    assert rc == M.RC_CREDENTIALS
    blob = "\n".join(texts)
    assert PASSWORD not in blob
    assert base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode() not in blob
    # ...while the credential *source* is still mentioned, so the hint stays actionable.
    assert "EARTHDATA_PASS" in blob


def test_plain_requests_session_is_the_fallback(monkeypatch) -> None:
    """Without earthaccess (and with no sockets), the factory still returns a usable session."""
    monkeypatch.setattr(M, "_earthaccess_session", lambda user, pwd: None)

    session = M.build_earthdata_session(USER, PASSWORD)

    assert callable(session.get)
    assert hasattr(session, "cookies")
    # Credentials are attached per hop by _open_stream, never baked into the session.
    assert getattr(session, "auth", None) is None


def test_earthaccess_session_credentials_are_not_baked_in(monkeypatch) -> None:
    """An earthaccess session arrives with `.auth` set for all hosts; that must be cleared so the
    credentials cannot reach the presigned S3 URL."""

    class EarthaccessSession:
        def __init__(self) -> None:
            self.auth = (USER, PASSWORD)
            self.headers: dict[str, str] = {}
            self.cookies: dict[str, str] = {"_urs-user": "cookie"}

        def get(self, *args, **kwargs):  # pragma: no cover - never called here
            raise AssertionError("no sockets in tests")

    earthaccess_session = EarthaccessSession()
    monkeypatch.setattr(M, "_earthaccess_session", lambda user, pwd: earthaccess_session)

    session = M.build_earthdata_session(USER, PASSWORD)

    assert session is earthaccess_session
    assert session.auth is None
    assert session.cookies  # the EDL cookies are the reason to keep earthaccess's session
    assert session.headers["User-Agent"].startswith("SAR_NASA/")
