#!/usr/bin/env python3
"""Phase 6 — видеоассеты и Tier 1. Тесты A-L.

Тесты работают с НАСТОЯЩИМИ видеофайлами: короткие ролики собираются здесь
же ffmpeg-ом во временный каталог. Это не подмена данных аккаунта — ни одна
строка отсюда не попадает ни в data/, ни в манифест ассетов. Синтетический
файл нужен ровно затем, чтобы проверить экстрактор на файле, а не на
предположении о файле.

Если инструментов промера в окружении нет, тесты не пропускаются: они
проверяют контракт деградации — экстрактор обязан вернуть причину
недоступности, а не значение по умолчанию.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from assets import policies as A
from assets import probe as PR
from assets import run as assets_run
from core import config
from features import visual_engine as E
from features import visual_policies as V
from assets import ingest as ING
from features import visual_run as VR

RESULTS = []
TOOLS = PR.tooling()
HAVE_TOOLING = TOOLS["cv2"] is not None and TOOLS["ffmpeg"] is not None
TMP = None
FIXTURES = {}


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<64}{detail}")


def _role_exists(name):
    with psycopg.connect(config.dsn("owner")) as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
        return cur.fetchone() is not None


def expect_error(cur, sql, params, fragment, name):
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute(sql, params)
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        check(name, False, "вставка прошла, хотя должна была быть отвергнута")
    except psycopg.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        check(name, fragment in str(e), str(e).split("\n")[0][:58])


def build_fixtures():
    """Настоящие видеофайлы: 2 плана со звуком, 1 план без звука, брак."""
    global TMP, FIXTURES
    TMP = Path(tempfile.mkdtemp(prefix="phase6_"))
    FIXTURES = {k: TMP / k for k in
                ("two_shots.mp4", "one_shot.mp4", "truncated.mp4",
                 "notvideo.mp4", "empty.mp4", "copy_of_two_shots.mp4")}
    FIXTURES["notvideo.mp4"].write_bytes(b"not a video, plain bytes only")
    FIXTURES["empty.mp4"].write_bytes(b"")
    if not HAVE_TOOLING:
        return False
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    ok = subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=3,format=yuv420p",
         "-f", "lavfi", "-i", "color=c=white:s=360x640:d=3,format=yuv420p",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-map", "2:a", "-r", "30", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(FIXTURES["two_shots.mp4"])], capture_output=True).returncode == 0
    ok &= subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=2,format=yuv420p",
         "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(FIXTURES["one_shot.mp4"])], capture_output=True).returncode == 0
    if ok:
        data = FIXTURES["two_shots.mp4"].read_bytes()
        FIXTURES["copy_of_two_shots.mp4"].write_bytes(data)
        FIXTURES["truncated.mp4"].write_bytes(data[:len(data) // 3])
    return ok


def fake_asset(video_id, path, version=1):
    """Строка манифеста из настоящего промера — как её сделал бы ингест."""
    r = PR.probe(path)
    return {
        "video_id": video_id, "asset_version": version,
        "asset_uid": A.asset_uid(r.get("sha256")),
        "source": "owner_upload", "source_uri": str(path),
        "sha256": r.get("sha256"), "byte_size": r.get("byte_size"),
        "mime_type": r.get("mime_type"), "duration_sec": r.get("duration_sec"),
        "width": r.get("width"), "height": r.get("height"), "fps": r.get("fps"),
        "frame_count": r.get("frame_count"), "acquired_at": "2026-09-17T00:00:00+00:00",
        "extractor_version": r["extractor_version"],
        "policy_version": A.ASSET_POLICY_VERSION,
        "asset_status": "valid" if r["ok"] else "invalid",
        "status_reason": r["status_reason"],
        "evidence_refs": ["file:test"],
        "extra": {"probe": {k: v for k, v in r.items()
                            if k in ("scene", "audio_present", "tooling",
                                     "declared_frame_count", "detail")}},
    }


# ══════════════════════════════════════════════════════════════════════ A
def test_A_sha_identity():
    print("\nA — идентичность ассета по SHA-256")
    if HAVE_TOOLING:
        a = PR.probe(FIXTURES["two_shots.mp4"])
        b = PR.probe(FIXTURES["copy_of_two_shots.mp4"])
        check("A1 разные пути, одинаковые байты — один sha256",
              a["sha256"] == b["sha256"], a["sha256"][:16])
        check("A2 одинаковый sha256 даёт один asset_uid",
              A.asset_uid(a["sha256"]) == A.asset_uid(b["sha256"]))
        c = PR.probe(FIXTURES["one_shot.mp4"])
        check("A3 разное содержимое — разные sha256", a["sha256"] != c["sha256"])
        check("A4 asset_uid детерминирован между прогонами",
              A.asset_uid(a["sha256"]) == A.asset_uid(a["sha256"]))
    else:
        r = PR.probe(FIXTURES["notvideo.mp4"])
        check("A1 без инструментов sha256 всё равно считается",
              isinstance(r.get("sha256"), str) and len(r["sha256"]) == 64)
        check("A2 asset_uid выводится из sha256",
              A.asset_uid(r["sha256"]) == A.asset_uid(r["sha256"]))
        check("A3 хеш пустого файла отличается",
              PR.probe(FIXTURES["empty.mp4"]).get("sha256") != r.get("sha256"))
        check("A4 asset_uid отсутствующего файла — None",
              A.asset_uid(None) is None)
    check("A5 asset_uid без хеша не выдумывается", A.asset_uid(None) is None)
    real = assets_run.load_manifest()
    bad = [r for r in real if r["sha256"] and r["asset_uid"] != A.asset_uid(r["sha256"])]
    check("A6 в реальном манифесте все asset_uid выведены из sha256",
          not bad, f"нарушений {len(bad)}")


# ══════════════════════════════════════════════════════════════════════ B
def test_B_duplicate_detection():
    print("\nB — повтор содержимого не создаёт второй ассет")
    if not HAVE_TOOLING:
        check("B1 инструментов нет: проверяется чистая политика дублей", True,
              "деградация")
    recs = []
    if HAVE_TOOLING:
        first = fake_asset("vid-1", FIXTURES["two_shots.mp4"], 1)
        recs.append(first)
        dup = A.duplicate_of(recs, "vid-1", first["sha256"])
        check("B1 тот же файл распознан как дубль", dup is not None)
        check("B2 дубль указывает на прежнюю версию",
              dup["asset_version"] == 1)
        other = fake_asset("vid-1", FIXTURES["one_shot.mp4"],
                           A.next_version(recs, "vid-1"))
        check("B3 другой файл получает новую версию",
              other["asset_version"] == 2)
        recs.append(other)
        check("B4 у ролика теперь две версии",
              len([r for r in recs if r["video_id"] == "vid-1"]) == 2)
        check("B5 текущей стала последняя валидная",
              A.current_asset(recs, "vid-1")["asset_version"] == 2)
        same_bytes_other_video = fake_asset("vid-2", FIXTURES["copy_of_two_shots.mp4"], 1)
        check("B6 те же байты под другим роликом — тот же asset_uid",
              same_bytes_other_video["asset_uid"] == first["asset_uid"])
        check("B7 но дублем для другого ролика не считаются",
              A.duplicate_of(recs, "vid-2", first["sha256"]) is None)
    check("B8 без хеша дубль не ищется",
          A.duplicate_of(recs, "vid-1", None) is None)

    # повторный прогон реального ингеста ничего не добавляет
    rows_before = assets_run.load_manifest()
    _, added, _, _, _ = assets_run.build(write=False)
    check("B9 повторный ингест реальных данных не добавляет строк",
          added == [], f"добавлено {len(added)}")
    check("B10 манифест на диске не тронут",
          assets_run.load_manifest() == rows_before)

    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("""SELECT count(*) FROM (
              SELECT video_id, sha256 FROM video_assets WHERE sha256 IS NOT NULL
               GROUP BY 1,2 HAVING count(*) > 1) d""")
        check("B11 в БД нет двух строк с одним содержимым на ролик",
              cur.fetchone()[0] == 0)


# ══════════════════════════════════════════════════════════════════════ C
def test_C_invalid_asset_rejected():
    print("\nC — битый или неполный файл не становится текущим")
    cases = [("empty.mp4", A.REASON_EMPTY), ("notvideo.mp4", A.REASON_UNKNOWN_CONTAINER)]
    if HAVE_TOOLING:
        cases.append(("truncated.mp4", None))    # причина зависит от декодера
    for name, expected in cases:
        r = PR.probe(FIXTURES[name])
        check(f"C1 {name}: промер отказывает", r["ok"] is False,
              str(r["status_reason"]))
        check(f"C2 {name}: причина машинная",
              r["status_reason"] in A.INVALID_REASONS, str(r["status_reason"]))
        if expected:
            check(f"C3 {name}: причина именно {expected}",
                  r["status_reason"] == expected, str(r["status_reason"]))

    bad = {"video_id": "vid-x", "asset_version": 1, "asset_uid": None,
           "source": "owner_upload", "source_uri": "x.mp4", "sha256": None,
           "byte_size": None, "mime_type": None, "duration_sec": None,
           "width": None, "height": None, "fps": None, "frame_count": None,
           "acquired_at": None, "extractor_version": "t", "policy_version": "t",
           "asset_status": "invalid", "status_reason": A.REASON_DECODE_FAILED,
           "evidence_refs": ["file:x"], "extra": {}}
    check("C4 битая строка проходит валидацию политики как invalid",
          A.validate_record(bad) == [], str(A.validate_record(bad)))
    check("C5 битый ассет текущим не становится",
          A.current_asset([bad], "vid-x") is None)
    liar = {**bad, "asset_status": "valid"}
    check("C6 объявить битый ассет валидным политика не даёт",
          any("без промеров" in v for v in A.validate_record(liar)),
          str(A.validate_record(liar))[:50])

    with psycopg.connect(config.dsn("owner"), autocommit=False) as c, c.cursor() as cur:
        cur.execute("SELECT video_id FROM videos ORDER BY video_id LIMIT 1")
        vid = cur.fetchone()[0]
        sql = """INSERT INTO video_assets (video_id, asset_uid, asset_version,
              source, source_uri, sha256, byte_size, mime_type, duration_sec,
              width, height, fps, frame_count, acquired_at, extractor_version,
              policy_version, asset_status, status_reason, evidence_refs,
              registered_at, run_id)
            VALUES (%(vid)s, %(uid)s, %(ver)s, %(src)s, 'x.mp4', %(sha)s,
              %(size)s, %(mime)s, %(dur)s, %(w)s, %(h)s, %(fps)s, %(fc)s,
              now(), 't', 't', %(st)s, %(reason)s, ARRAY['e'], now(),
              gen_random_uuid())"""
        full = {"vid": vid, "uid": "u", "ver": 900, "src": "owner_upload",
                "sha": "a" * 64, "size": 10, "mime": "video/mp4", "dur": 1.0,
                "w": 10, "h": 10, "fps": 30.0, "fc": 30, "st": "valid",
                "reason": None}
        expect_error(cur, sql, {**full, "dur": None},
                     "ck_asset_valid_complete",
                     "C7 БД отвергает valid без длительности")
        expect_error(cur, sql, {**full, "sha": None},
                     "ck_asset_valid_complete",
                     "C8 БД отвергает valid без sha256")
        expect_error(cur, sql, {**full, "st": "invalid", "reason": None},
                     "ck_asset_reason",
                     "C9 БД отвергает invalid без причины")
        expect_error(cur, sql, {**full, "st": "missing", "src": "not_supplied",
                                "reason": "нет файла"},
                     "ck_asset_missing_no_hash",
                     "C10 БД отвергает missing с хешем")
        cur.execute("SAVEPOINT ok")
        cur.execute(sql, full)
        cur.execute("""SELECT count(*) FROM video_assets_current
                        WHERE video_id = %s""", (vid,))
        check("C11 валидный ассет становится текущим", cur.fetchone()[0] == 1)
        cur.execute(sql, {**full, "ver": 901, "sha": "b" * 64,
                          "st": "invalid", "reason": "битый",
                          "dur": None, "w": None, "h": None, "fps": None,
                          "fc": None, "size": None, "mime": None})
        cur.execute("""SELECT asset_version FROM video_assets_current
                        WHERE video_id = %s""", (vid,))
        rows = [r[0] for r in cur.fetchall()]
        check("C12 более поздний битый ассет НЕ вытесняет валидный",
              rows == [900], str(rows))
        c.rollback()


# ══════════════════════════════════════════════════════════════════════ D
def test_D_features_append_only():
    print("\nD — признаки append-only")
    with psycopg.connect(config.dsn("rw"), autocommit=True) as c, c.cursor() as cur:
        for op, sql in (("UPDATE", "UPDATE video_features SET feature_value='x' WHERE FALSE"),
                        ("DELETE", "DELETE FROM video_features WHERE FALSE"),
                        ("UPDATE assets", "UPDATE video_assets SET source_uri='x' WHERE FALSE"),
                        ("DELETE assets", "DELETE FROM video_assets WHERE FALSE")):
            try:
                cur.execute(sql)
                check(f"D1 рантайм не может {op}", False, "прошло")
            except psycopg.Error as e:
                check(f"D1 рантайм не может {op}", "permission denied" in str(e))

    # Права — первый замок. Триггер — второй, НЕЗАВИСИМЫЙ: он обязан закрыть
    # роль, которой UPDATE выдали по ошибке. Проверяется именно этот случай:
    # грант временно выдаётся, операция всё равно не проходит, грант
    # снимается в finally и отсутствие проверяется отдельно.
    # Владельца таблицы триггер пропускает намеренно: иначе миграции,
    # которым правка нужна законно, стали бы невозможны.
    granted = []
    try:
        with psycopg.connect(config.dsn("owner"), autocommit=True) as c, c.cursor() as cur:
            for t in ("video_assets", "video_features"):
                cur.execute(f"GRANT UPDATE, DELETE ON {t} TO tiktok_rw")
                granted.append(t)
        with psycopg.connect(config.dsn("rw"), autocommit=False) as c, c.cursor() as cur:
            for t in granted:
                for op, sql in (("UPDATE", f"UPDATE {t} SET run_id = run_id"),
                                ("DELETE", f"DELETE FROM {t}")):
                    cur.execute("SAVEPOINT sp")
                    try:
                        cur.execute(sql)
                        cur.execute("ROLLBACK TO SAVEPOINT sp")
                        check(f"D2 триггер закрывает {op} {t} при выданном гранте",
                              False, "прошло")
                    except psycopg.Error as e:
                        cur.execute("ROLLBACK TO SAVEPOINT sp")
                        check(f"D2 триггер закрывает {op} {t} при выданном гранте",
                              "APPEND_ONLY" in str(e), str(e).split("\n")[0][:46])
            c.rollback()
    finally:
        with psycopg.connect(config.dsn("owner"), autocommit=True) as c, c.cursor() as cur:
            for t in granted:
                cur.execute(f"REVOKE UPDATE, DELETE ON {t} FROM tiktok_rw")

    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.role_table_grants
                        WHERE grantee='tiktok_rw'
                          AND table_name IN ('video_assets','video_features')
                          AND privilege_type IN ('UPDATE','DELETE')""")
        check("D3 временный грант снят полностью", cur.fetchone()[0] == 0)

    with psycopg.connect(config.dsn("rw"), autocommit=True) as c, c.cursor() as cur:
        for t in ("video_assets", "video_features"):
            try:
                cur.execute(f"UPDATE {t} SET run_id = run_id WHERE FALSE")
                check(f"D4 права на {t} восстановлены (UPDATE закрыт)", False,
                      "прошло")
            except psycopg.Error as e:
                check(f"D4 права на {t} восстановлены (UPDATE закрыт)",
                      "permission denied" in str(e))


# ══════════════════════════════════════════════════════════════════════ E
def test_E_version_coexistence():
    print("\nE — версии экстрактора и политики сосуществуют")
    with psycopg.connect(config.dsn("owner"), autocommit=False) as c, c.cursor() as cur:
        cur.execute("""SELECT a.attname FROM pg_constraint k
              JOIN unnest(k.conkey) u(attnum) ON TRUE
              JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=u.attnum
             WHERE k.conname='uq_video_feature_version' ORDER BY a.attnum""")
        cols = {r[0] for r in cur.fetchall()}
        check("E1 идентичность включает политику И экстрактор",
              cols == {"video_id", "feature_name", "policy_version",
                       "extractor_version"}, str(sorted(cols)))

        cur.execute("SELECT video_id FROM videos ORDER BY video_id LIMIT 1")
        vid = cur.fetchone()[0]
        ins = """INSERT INTO video_features (video_id, feature_name, feature_value,
              feature_value_numeric, feature_type, feature_status, tier,
              policy_version, extractor_version, source_basis,
              reconciliation_basis, evidence_refs, computed_from, computed_at,
              run_id)
            VALUES (%s,'e_probe_test','1',1,'numeric','observed','tier_1',
              %s,%s,'{}'::jsonb,'{}'::jsonb,ARRAY['e'],ARRAY['x'],%s,
              gen_random_uuid())"""
        cur.execute(ins, (vid, "p-1.0.0", "x-1.0.0", "2026-09-17 10:00+00"))
        cur.execute(ins, (vid, "p-1.0.0", "x-2.0.0", "2026-09-17 11:00+00"))
        cur.execute(ins, (vid, "p-2.0.0", "x-2.0.0", "2026-09-17 12:00+00"))
        cur.execute("""SELECT count(*) FROM video_features
                        WHERE feature_name='e_probe_test'""")
        check("E2 три версии сосуществуют", cur.fetchone()[0] == 3)
        expect_error(cur, ins, (vid, "p-1.0.0", "x-1.0.0", "2026-09-17 13:00+00"),
                     "uq_video_feature_version",
                     "E3 повтор той же пары версий отвергается")
        cur.execute("""SELECT policy_version, extractor_version
                         FROM video_features_current
                        WHERE feature_name='e_probe_test'""")
        cur_rows = cur.fetchall()
        check("E4 действующее значение ровно одно", len(cur_rows) == 1,
              str(cur_rows))
        check("E5 действующим стал последний расчёт",
              cur_rows == [("p-2.0.0", "x-2.0.0")], str(cur_rows))
        c.rollback()

    tiers = {r["tier"] for r in VR.build(write=False)[0]}
    check("E6 Tier 1 не смешан с прежними тирами", tiers == {"tier_1"}, str(tiers))
    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("SELECT tier, count(*) FROM video_features GROUP BY 1 ORDER BY 1")
        by_tier = dict(cur.fetchall())
        check("E7 прежние 720 строк Tier 0/0.5 на месте",
              by_tier.get("tier_0", 0) == 480 and by_tier.get("tier_0.5", 0) == 240,
              str(by_tier))


# ══════════════════════════════════════════════════════════════════════ F
def test_F_unavailable_is_not_false():
    print("\nF — unavailable не равен false")
    rows, _, _, _ = VR.build(write=False)
    absent = [r for r in rows if r["feature_status"] in V.ABSENT_STATUS]
    check("F1 отсутствующих признаков много", len(absent) > 0, str(len(absent)))
    check("F2 ни у одного отсутствующего нет значения",
          all(r["feature_value"] is None for r in absent))
    falsy = [r for r in absent if r["feature_value"] in ("false", "0", "")]
    check("F3 отсутствие ни разу не записано как false/0", not falsy,
          f"нарушений {len(falsy)}")
    check("F4 у каждого отсутствующего есть причина",
          all(r["status_reason"] for r in absent))
    check("F5 тип отсутствующего признака равен статусу, а не типу значения",
          all(r["feature_type"] == r["feature_status"] for r in absent))
    bools = [r for r in rows if V.TIER_1_FEATURES[r["feature_name"]]["type"] == "boolean"]
    unavailable_bools = [r for r in bools if r["feature_status"] in V.ABSENT_STATUS]
    check("F6 булевы признаки без данных не превращаются в false",
          all(r["feature_value"] is None for r in unavailable_bools),
          f"{len(unavailable_bools)} булевых без данных")

    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("""SELECT count(*) FROM video_features
                        WHERE tier='tier_1'
                          AND feature_status IN ('unavailable','invalid_asset',
                                                 'insufficient_evidence')
                          AND (feature_value IS NOT NULL
                               OR feature_value_bool IS NOT NULL
                               OR feature_value_numeric IS NOT NULL)""")
        check("F7 в БД у отсутствующих признаков все значения NULL",
              cur.fetchone()[0] == 0)
        cur.execute("""SELECT count(*) FROM video_features
                        WHERE tier='tier_1' AND feature_value_bool = FALSE""")
        check("F8 в БД нет Tier 1 признака со значением false",
              cur.fetchone()[0] == 0, "при отсутствии ассетов")


# ══════════════════════════════════════════════════════════════════════ G
def test_G_evidence_required():
    print("\nG — признак без основания не принимается")
    rows, _, _, _ = VR.build(write=False)
    check("G1 у каждой строки есть evidence_refs",
          all(r["evidence_refs"] for r in rows))
    check("G2 у каждой строки назван экстрактор",
          all(r["extractor_version"] for r in rows))
    check("G3 у каждой строки назван source_basis с состоянием ассета",
          all("asset_status" in r["source_basis"] for r in rows))
    check("G4 цепочка прослеживается: video_id -> asset -> признак",
          all(r["video_id"] and r["feature_name"] and r["policy_version"]
              for r in rows))
    present = [r for r in rows if r["feature_status"] not in V.ABSENT_STATUS]
    check("G5 у значений указано, из чего они посчитаны",
          all(r["computed_from"] for r in present),
          f"со значением: {len(present)}")

    if HAVE_TOOLING:
        asset = fake_asset("vid-e", FIXTURES["two_shots.mp4"])
        frows = E.features_for_video("vid-e", [asset], asset["extractor_version"])
        vals = {r["feature_name"]: r for r in frows
                if r["feature_status"] not in V.ABSENT_STATUS}
        check("G6 на настоящем файле признаки получают значения",
              len(vals) > 0, f"{len(vals)} из {len(frows)}")
        check("G7 каждое значение ссылается на sha256 ассета",
              all(any(asset["sha256"] in e for e in r["evidence_refs"])
                  for r in vals.values()))
        frame_based = [r for r in vals.values()
                       if r["feature_group"] in ("visual_structure", "opening")]
        check("G8 кадровые признаки называют использованные кадры",
              all(any(e.startswith("frames:") for e in r["evidence_refs"])
                  for r in frame_based), f"кадровых: {len(frame_based)}")
        opening = [r for r in vals.values() if r["feature_group"] == "opening"]
        check("G9 признаки начала называют временной отрезок",
              all(any(e.startswith("time_range:") for e in r["evidence_refs"])
                  for r in opening), f"начальных: {len(opening)}")
        check("G10 source_basis каждого значения несёт sha256 ассета",
              all(r["source_basis"]["asset_sha256"] == asset["sha256"]
                  for r in vals.values()))
    else:
        check("G6 без инструментов значений не появляется",
              not present, f"со значением: {len(present)}")


# ══════════════════════════════════════════════════════════════════════ H
def test_H_semantic_leakage_blocked():
    print("\nH — подпись и хештеги не создают семантический признак")
    caption_only = {"caption": "#gulyash Великая связь", "caption_len": 29,
                    "hashtags": ["gulyash"], "url": "https://tiktok.com/x",
                    "views": 12000, "engagement_rate": 0.031}
    for name in sorted(V.SEMANTIC_FEATURES):
        ok, code, detail = V.semantic_claim_allowed(name, caption_only)
        check(f"H1 {name} из одной подписи запрещён",
              ok is False and code == V.SEMANTIC_REASON_CODE, code or "-")
    ok, _, detail = V.semantic_claim_allowed("topic", caption_only)
    check("H2 названы использованные недостаточные источники",
          set(detail["insufficient_sources_used"])
          >= {"caption", "caption_len", "views"},
          str(detail["insufficient_sources_used"]))
    check("H3 названо, что именно требуется",
          "asset_sha256" in detail["required"])
    check("H4 перечислено, куда признак не имеет права попасть",
          set(detail["forbidden"]) == set(V.SEMANTIC_PROMOTION_TARGETS))
    try:
        V.assert_semantic_claim_allowed("hook", caption_only)
        check("H5 жёсткая форма поднимает исключение", False, "исключения нет")
    except V.SemanticLeakage as e:
        check("H5 жёсткая форма поднимает исключение",
              V.SEMANTIC_REASON_CODE in str(e))
    ok, _, _ = V.semantic_claim_allowed("hook", {"asset_sha256": "a" * 64})
    check("H6 при ссылке на промеренный файл запрет снимается", ok is True)
    ok, _, _ = V.semantic_claim_allowed("hook", {"asset_sha256": "нехеш"})
    check("H7 подделка под хеш не проходит", ok is False)
    ok, _, _ = V.semantic_claim_allowed("asset_width", caption_only)
    check("H8 несемантический признак подписью не ограничен", ok is True)

    for target in V.SEMANTIC_PROMOTION_TARGETS:
        allowed, code = V.semantic_promotion_allowed("topic", target, False)
        check(f"H9 без видео topic не попадает в {target}",
              allowed is False and code == V.SEMANTIC_REASON_CODE)

    with psycopg.connect(config.dsn("owner"), autocommit=False) as c, c.cursor() as cur:
        cur.execute("SELECT feature_name FROM semantic_feature_names ORDER BY 1")
        db_names = {r[0] for r in cur.fetchall()}
        check("H10 реестр в БД совпадает с реестром в коде",
              db_names == set(V.SEMANTIC_FEATURES), str(sorted(db_names)))
        cur.execute("SELECT video_id FROM videos ORDER BY video_id LIMIT 1")
        vid = cur.fetchone()[0]
        ins = """INSERT INTO video_features (video_id, feature_name, feature_value,
              feature_type, feature_status, tier, policy_version,
              extractor_version, source_basis, reconciliation_basis,
              evidence_refs, computed_from, computed_at, run_id, status_reason)
            VALUES (%s,%s,%s,%s,%s,'tier_1','p','x',
              %s::jsonb,'{}'::jsonb,ARRAY['e'],ARRAY['caption'],now(),
              gen_random_uuid(), 'тест')"""
        expect_error(cur, ins, (vid, "topic", "юмор", "categorical", "observed",
                                json.dumps(caption_only)),
                     "SEMANTIC_WITHOUT_VIDEO_EVIDENCE",
                     "H11 БД отвергает topic, обоснованный подписью")
        expect_error(cur, ins, (vid, "hook", "вопрос", "categorical", "derived",
                                json.dumps({"caption": "x"})),
                     "SEMANTIC_WITHOUT_VIDEO_EVIDENCE",
                     "H12 БД отвергает hook без ассета")
        expect_error(cur, ins, (vid, "emotion", "радость", "categorical",
                                "observed", json.dumps({"asset_sha256": "c" * 64})),
                     "не зарегистрирован валидным ассетом",
                     "H13 БД отвергает ссылку на несуществующий ассет")
        cur.execute("SAVEPOINT okp")
        cur.execute(ins, (vid, "topic", None, "unavailable", "unavailable",
                          json.dumps({"asset_status": "no_asset"})))
        check("H14 честное unavailable по семантике разрешено", True)
        cur.execute("ROLLBACK TO SAVEPOINT okp")

        cur.execute("""INSERT INTO dna_versions (account_id, version, built_at,
              n_videos, n_claims, run_id)
            SELECT account_id, 998, now(), 16, 0, gen_random_uuid()
              FROM accounts LIMIT 1 RETURNING dna_version_id""")
        dv = cur.fetchone()[0]
        cur.execute("SELECT account_id FROM accounts LIMIT 1")
        acc = cur.fetchone()[0]
        dna = """INSERT INTO content_dna (account_id, dna_version_id, version,
              built_at, created_at, n_videos, section, statement, claim_type,
              n_sample, min_sample_required, evidence_count, strength, source,
              source_count, reconciliation_status, evidence)
            VALUES (%s,%s,998,now(),now(),16,'topic_patterns',%s,'HYPOTHESIS',
              16,25,3,'weak','supermetrics',1,'single_source',%s::jsonb)"""
        expect_error(cur, dna, (acc, dv, "тема из подписи",
                                json.dumps({"video_ids": ["a", "b", "c"],
                                            "snapshot_ids": [1, 2, 3],
                                            "feature_names": ["topic"]})),
                     "SEMANTIC_WITHOUT_VIDEO_EVIDENCE",
                     "H15 Content DNA не принимает topic без видеодоказательства")
        cur.execute("SAVEPOINT okd")
        cur.execute(dna, (acc, dv, "несемантический признак",
                          json.dumps({"video_ids": ["a", "b", "c"],
                                      "snapshot_ids": [1, 2, 3],
                                      "feature_names": ["asset_width"]})))
        check("H16 несемантический признак в Content DNA проходит", True)
        cur.execute("ROLLBACK TO SAVEPOINT okd")

        cur.execute("""SELECT count(*) FROM semantic_annotations
                        WHERE is_authoritative = TRUE""")
        check("H17 слой аннотаций не может быть авторитетным",
              cur.fetchone()[0] == 0)
        expect_error(cur, """INSERT INTO semantic_annotations (video_id,
              asset_sha256, feature_name, annotation_value, annotator,
              annotator_version, evidence, policy_version, created_at, run_id,
              is_authoritative)
            VALUES (%s, %s, 'topic', 'юмор', 'llm_vision', 'v1',
              %s::jsonb, 'p', now(), gen_random_uuid(), TRUE)""",
            (vid, "d" * 64, json.dumps({"frame_indices": [0, 30]})),
            "ck_sa_not_authoritative",
            "H18 аннотацию нельзя объявить авторитетной")
        c.rollback()


# ══════════════════════════════════════════════════════════════════════ I
def test_I_deterministic_extraction():
    print("\nI — повторное извлечение даёт тот же результат")
    r1 = VR.build(write=False)
    r2 = VR.build(write=False)
    check("I1 tier1_hash воспроизводится", r1[1] == r2[1], r1[1][:16])
    check("I2 tier1_run_id воспроизводится", r1[2] == r2[2], r1[2][:8])
    check("I3 строки совпадают побайтово", r1[0] == r2[0])
    manifest = json.loads((ROOT / "data" / "features_tier1" / "manifest.json")
                          .read_text(encoding="utf-8"))
    check("I4 записанный на диск hash совпадает с пересчитанным",
          manifest["tier1_hash"] == r1[1], manifest["tier1_hash"][:16])

    a1 = assets_run.build(write=False)
    a2 = assets_run.build(write=False)
    check("I5 asset_hash воспроизводится", a1[3] == a2[3], a1[3][:16])

    if HAVE_TOOLING:
        p1 = PR.probe(FIXTURES["two_shots.mp4"])
        p2 = PR.probe(FIXTURES["two_shots.mp4"])
        check("I6 промер одного файла воспроизводится побайтово", p1 == p2)
        check("I7 сцены посчитаны верно: два плана -> одна смена",
              p1["scene"]["shot_count"] == 2
              and p1["scene"]["scene_change_count"] == 1,
              f"shots={p1['scene']['shot_count']}")
        p3 = PR.probe(FIXTURES["one_shot.mp4"])
        check("I8 один план -> ноль смен",
              p3["scene"]["shot_count"] == 1
              and p3["scene"]["scene_change_count"] == 0)
        check("I9 звук распознан там, где он есть", p1["audio_present"] is True)
        check("I10 и отсутствует там, где его нет", p3["audio_present"] is False)
        check("I11 копия файла даёт тот же промер, кроме пути",
              {k: v for k, v in p1.items() if k != "detail"}
              == {k: v for k, v in PR.probe(FIXTURES["copy_of_two_shots.mp4"]).items()
                  if k != "detail"})
        a = fake_asset("vid-d", FIXTURES["two_shots.mp4"])
        f1 = E.features_for_video("vid-d", [a], a["extractor_version"])
        f2 = E.features_for_video("vid-d", [a], a["extractor_version"])
        check("I12 признаки из одного промера воспроизводятся", f1 == f2)
        names = {r["feature_name"]: r["feature_value"] for r in f1}
        check("I13 геометрия прочитана из файла",
              names["asset_width"] == "360" and names["asset_height"] == "640",
              f"{names['asset_width']}x{names['asset_height']}")
        check("I14 shot_count = 2 на файле из двух планов",
              names["shot_count"] == "2", str(names["shot_count"]))
        check("I15 audio_present = true", names["audio_present"] == "true")
    else:
        check("I6 без инструментов промер отказывает с причиной",
              PR.probe(FIXTURES["notvideo.mp4"])["status_reason"]
              in A.INVALID_REASONS)


# ══════════════════════════════════════════════════════════════════════ J
def test_J_missing_video_no_fabrication():
    print("\nJ — отсутствие видео не рождает выдуманных признаков")
    rows, _, _, by_status = VR.build(write=False)
    assets = assets_run.load_manifest()
    videos = assets_run.load_videos()
    with_asset = [v for v in videos if A.current_asset(assets, v["video_id"])]
    check("J1 роликов с валидным ассетом столько, сколько файлов",
          len(with_asset) == 0, f"{len(with_asset)} из {len(videos)}")
    check("J2 при нуле файлов нет ни одного наблюдённого признака",
          by_status.get("observed", 0) == 0 and by_status.get("derived", 0) == 0,
          str(dict(by_status)))
    check("J3 каждая строка честно помечена отсутствием",
          all(r["feature_status"] in V.ABSENT_STATUS for r in rows))
    check("J4 причина ссылается на отсутствие ассета",
          all(V.NO_ASSET in (r["status_reason"] or "")
              or V.NO_DETECTOR in (r["status_reason"] or "")
              or V.NO_OCR in (r["status_reason"] or "")
              for r in rows))
    check("J5 покрыты все ролики и все объявленные признаки",
          len(rows) == len(videos) * len(V.TIER_1_FEATURES),
          f"{len(rows)} = {len(videos)}x{len(V.TIER_1_FEATURES)}")
    check("J6 отсутствие ассета зафиксировано явной строкой манифеста",
          len([a for a in assets if a["asset_status"] == "missing"]) == len(videos),
          str(len(assets)))
    check("J7 ни один отсутствующий ассет не имеет хеша",
          all(a["sha256"] is None for a in assets
              if a["asset_status"] == "missing"))
    check("J8 контракт ингеста записан в манифест",
          json.loads((ROOT / "data" / "assets" / "manifest.json")
                     .read_text(encoding="utf-8"))["ingest_contract"]
          == "data/assets/incoming/<video_id>.<ext>")
    check("J9 подпись ролика ни разу не попала в источник признака",
          not [r for r in rows
               if any(s in json.dumps(r["source_basis"], ensure_ascii=False)
                      for s in ("caption", "hashtag"))])


# ══════════════════════════════════════════════════════════════════════ K
def test_K_phase51_guards_intact():
    print("\nK — защиты Phase 5.1 продолжают работать")
    from insights import pipeline
    from insights import policies as P
    from insights import run as R
    ok, code, _ = P.promotion_allowed("duration_sec", "completion_rate", "FACT")
    check("K1 механическая пара по-прежнему закрыта",
          ok is False and code == "mechanically_dependent")
    ev = pipeline.evaluate_association(
        {"x_metric": "duration_sec", "y_metric": "completion_rate",
         "rho": -0.99, "p_two_sided": 1e-9, "n": 16, "status": "measured",
         "coverage_ratio": 1.0, "sample_status": "insufficient_sample"})
    check("K2 значимость по-прежнему не обходит механический гейт",
          ev["stopped_at"] == "mechanical_dependency"
          and "statistical_significance" not in ev["executed"])
    ins, blocked, md, h, run_id, hashes = R.build(write=False)
    check("K3 insights_hash не изменился Phase 6",
          h == "d22e195409886f8e9fc563600b4ad61eb4aac0db299978390550cbcc13e92802",
          h[:16])
    check("K4 выводов по-прежнему 11, блокировок 28",
          len(ins) == 11 and len(blocked) == 28,
          f"{len(ins)}/{len(blocked)}")
    check("K5 FACT по-прежнему ноль",
          not [i for i in ins if i["claim_type"] == "FACT"])
    check("K6 upstream-хеши не тронуты",
          hashes == {"analytics": "52fa355f77987c7aa8479e18a89616cd6c2b8e36dfaf49c477e2eca7b35d474f",
                     "features": "71075aaf4170be7b6b43abde9126227e621274ed6570839e5b37f23b42f3bd6b",
                     "reconciliation": "f52205acef19ba5082f192e7206ff9faa3917cde3be0e970f7afb4e0202506cf"},
          str({k: v[:8] for k, v in hashes.items()}))


# ══════════════════════════════════════════════════════════════════════ L
def test_L_publishing_disabled():
    print("\nL — публикация остаётся выключенной")
    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("""SELECT capability, enabled FROM system_capabilities
                        ORDER BY capability""")
        caps = dict(cur.fetchall())
        check("L1 publishing.submit = FALSE",
              caps.get("publishing.submit") is False, str(caps.get("publishing.submit")))
        check("L2 ни одна способность публикации не включена",
              not [k for k, v in caps.items() if k.startswith("publishing") and v],
              str({k: v for k, v in caps.items() if k.startswith("publishing")}))
        cur.execute("SELECT count(*) FROM publishing_queue")
        check("L3 очередь публикации пуста", cur.fetchone()[0] == 0)
        cur.execute("SELECT count(*) FROM publishing_history")
        check("L4 истории публикаций нет", cur.fetchone()[0] == 0)
    src = "\n".join((ROOT / p).read_text(encoding="utf-8") for p in
                    ("assets/run.py", "assets/probe.py", "assets/policies.py",
                     "features/visual_run.py", "features/visual_engine.py",
                     "features/visual_policies.py"))
    for bad in ("createScheduledPost", "publishing_queue", "system_capabilities",
                "requests.post", "urllib.request", "httpx"):
        check(f"L5 слой Phase 6 не обращается к {bad}", bad not in src)


# ══════════════════════════════════════════════════════════════════════ M
def test_M_local_ingestion_workflow():
    """M. Однокомандный локальный ингест: от файла до покрытия.

    Прогон идёт в ИЗОЛИРОВАННОМ каталоге: настоящие data/assets и
    data/features_tier1 не трогаются. Иначе тест оставил бы в append-only
    манифесте строку про синтетический ролик, и отчёт перестал бы описывать
    реальность.
    """
    print("\nM — локальный ингест одной командой")
    videos = assets_run.load_videos()
    real_id = sorted(v["video_id"] for v in videos)[0]

    if not HAVE_TOOLING:
        check("M1 без инструментов промера ингест всё равно отрабатывает",
              ING.run(load=False, verbose=False)["coverage"]
              ["video_asset_status"] == "UNAVAILABLE")
        return

    sandbox = Path(tempfile.mkdtemp(prefix="phase6_ingest_"))
    inc = sandbox / "incoming"
    inc.mkdir(parents=True)
    # настоящий видеофайл под именем настоящего video_id
    (inc / f"{real_id}.mp4").write_bytes(FIXTURES["two_shots.mp4"].read_bytes())
    # и три файла, которые ингест обязан отвергнуть громко, а не молча
    (inc / "9999999999999999999.mp4").write_bytes(b"x" * 10)     # чужой id
    (inc / f"{real_id}.txt").write_text("не видео")              # не то расширение
    (inc / ".gitkeep").write_text("служебный")

    saved = (assets_run.OUT, assets_run.INCOMING, assets_run.MANIFEST,
             VR.OUT, ING.COVERAGE_MD, ING.COVERAGE_JSON)
    try:
        assets_run.OUT = sandbox
        assets_run.INCOMING = inc
        assets_run.MANIFEST = sandbox / "manifest.jsonl"
        VR.OUT = sandbox / "tier1"
        ING.COVERAGE_MD = sandbox / "tier1" / "coverage.md"
        ING.COVERAGE_JSON = sandbox / "tier1" / "coverage.json"

        res = ING.run(load=False, verbose=False)
        scan, cov = res["scan"], res["coverage"]

        # 1-2. обнаружение и сопоставление
        check("M1 настоящий файл сопоставлен с video_id",
              list(scan["matched"]) == [real_id], str(list(scan["matched"])))
        check("M2 файл с чужим id назван несопоставленным, а не проигнорирован",
              scan["unmatched"] == ["9999999999999999999.mp4"],
              str(scan["unmatched"]))
        check("M3 неподдерживаемое расширение выделено отдельно",
              scan["unsupported"] == [f"{real_id}.txt"], str(scan["unsupported"]))
        check("M4 служебные файлы пропущены молча",
              scan["ignored"] == [".gitkeep"], str(scan["ignored"]))

        # 3-5. валидация, SHA-256, регистрация
        reg = [a for a in res["assets"] if a["video_id"] == real_id]
        check("M5 ролик зарегистрирован ровно одной строкой", len(reg) == 1)
        a = reg[0]
        check("M6 ассет валиден", a["asset_status"] == "valid", a["asset_status"])
        check("M7 SHA-256 посчитан",
              isinstance(a["sha256"], str) and len(a["sha256"]) == 64,
              (a["sha256"] or "")[:16])
        check("M8 промер заполнен полностью",
              all(a[k] is not None for k in A.REQUIRED_FOR_VALID),
              f"{a['width']}x{a['height']} @{a['fps']} {a['duration_sec']}c")
        check("M9 строка проходит валидатор политики",
              A.validate_record(a) == [], str(A.validate_record(a)))
        check("M10 остальные 15 роликов остались missing",
              sum(1 for x in res["assets"] if x["asset_status"] == "missing")
              == len(videos) - 1)

        # 6. Tier 1 на настоящем файле
        mine = [r for r in res["rows"] if r["video_id"] == real_id]
        valued = [r for r in mine if r["feature_status"] not in V.ABSENT_STATUS]
        check("M11 у ролика с файлом появились настоящие признаки",
              len(valued) > 0, f"{len(valued)} из {len(mine)}")
        vals = {r["feature_name"]: r["feature_value"] for r in valued}
        check("M12 геометрия прочитана из файла",
              vals.get("asset_width") == "360" and vals.get("asset_height") == "640",
              f"{vals.get('asset_width')}x{vals.get('asset_height')}")
        check("M13 структура посчитана: два плана -> одна смена",
              vals.get("shot_count") == "2"
              and vals.get("scene_change_count") == "1",
              f"shots={vals.get('shot_count')}")
        check("M14 звук распознан", vals.get("audio_present") == "true")
        check("M15 без детектора признак остался unavailable, а не false",
              all(r["feature_value"] is None for r in mine
                  if r["feature_name"] in ("face_present", "person_present",
                                           "speech_present", "text_present")))

        # 7. доказательства
        check("M16 у каждого значения есть asset_sha256 в основании",
              all(r["source_basis"]["asset_sha256"] == a["sha256"]
                  for r in valued))
        check("M17 у каждого значения есть extractor_version и policy_version",
              all(r["extractor_version"] and r["policy_version"] for r in valued))
        check("M18 кадровые признаки называют использованные кадры",
              all(any(e.startswith("frames:") for e in r["evidence_refs"])
                  for r in valued
                  if r["feature_group"] in ("visual_structure", "opening")))
        check("M19 оркестратор подтвердил полноту доказательств",
              res["evidence_ok"] is True)

        # 8. детерминизм
        check("M20 оркестратор подтвердил воспроизводимость",
              res["deterministic"] is True)
        again = ING.run(load=False, verbose=False)
        check("M21 повторный прогон даёт те же хеши",
              again["hashes"] == res["hashes"], res["hashes"]["tier1"][:16])
        check("M22 повторный прогон не добавляет ассетов",
              again["added"] == [], f"добавлено {len(again['added'])}")

        # 9. покрытие
        check("M23 статус стал PARTIAL при части файлов",
              cov["video_asset_status"] == "PARTIAL",
              cov["video_asset_status"])
        check("M24 покрытие считает ролики с ассетом",
              cov["n_videos_with_current_asset"] == 1)
        check("M25 заполненность больше нуля и меньше единицы",
              0 < cov["feature_fill_rate"] < 1,
              f"{100 * cov['feature_fill_rate']:.1f}%")
        check("M26 отчёт о покрытии записан",
              ING.COVERAGE_MD.exists() and ING.COVERAGE_JSON.exists())
        md = ING.COVERAGE_MD.read_text(encoding="utf-8")
        check("M27 отчёт называет статус и несопоставленный файл",
              "VIDEO_ASSET_STATUS: PARTIAL" in md
              and "9999999999999999999.mp4" in md)
        check("M28 отчёт перечисляет причины отсутствия",
              V.NO_DETECTOR in md or V.NO_OCR in md)
        check("M29 ни одна причина не ссылается на подпись или хештеги",
              not any(s in json.dumps(res["rows"], ensure_ascii=False)
                      for s in ('"caption"', '"hashtags"')))
    finally:
        (assets_run.OUT, assets_run.INCOMING, assets_run.MANIFEST,
         VR.OUT, ING.COVERAGE_MD, ING.COVERAGE_JSON) = saved

    # реальные артефакты не пострадали
    real = assets_run.load_manifest()
    check("M30 настоящий манифест не тронут тестом",
          len(real) == len(videos)
          and all(r["asset_status"] == "missing" for r in real),
          f"{len(real)} строк, все missing")
    check("M31 настоящий каталог приёма по-прежнему пуст",
          list(assets_run.scan_incoming()["matched"]) == [])


if __name__ == "__main__":
    built = build_fixtures()
    print(f"инструменты промера: " + ", ".join(
        f"{k}={v or 'ОТСУТСТВУЕТ'}" for k, v in sorted(TOOLS.items())))
    print(f"тестовые видеофайлы собраны: {built}")
    for fn in (test_A_sha_identity, test_B_duplicate_detection,
               test_C_invalid_asset_rejected, test_D_features_append_only,
               test_E_version_coexistence, test_F_unavailable_is_not_false,
               test_G_evidence_required, test_H_semantic_leakage_blocked,
               test_I_deterministic_extraction,
               test_J_missing_video_no_fabrication,
               test_K_phase51_guards_intact, test_L_publishing_disabled,
               test_M_local_ingestion_workflow):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
