#!/usr/bin/env python3
"""Порядок проверок для измеренной связи.

Порядок здесь не комментарий, а исполняемая последовательность. Причина
конкретная: пока гейты стояли россыпью, статистическая значимость
оказывалась самым сильным аргументом — связь, прошедшая по p-значению,
шла дальше, и механическая природа пары выяснялась уже после. Порядок
переворачивает приоритет: сначала выясняется, ЧТО измерено, и только
потом — насколько уверенно.

    1. data_eligibility          — годятся ли данные вообще
    2. sample_size               — хватает ли наблюдений
    3. mechanical_dependency     — измеряется аудитория или устройство метрик
    4. outlier_robustness        — держится ли связь без выбросов
    5. statistical_significance  — отличима ли связь от шума
    6. hypothesis_generation     — выпуск гипотезы
    7. recommendation_generation — выпуск рекомендации

Проверка, остановившая конвейер, не даёт следующим выполниться вообще.
Поэтому «значимость обошла механический гейт» — не вопрос дисциплины
автора, а состояние, которого в записи стадий не может возникнуть.
"""
from analytics import policies as A
from analytics.association import MIN_N_FOR_ASSOCIATION
from insights import policies as P

PIPELINE_VERSION = "insights-pipeline-1.0.0"

CHECK_ORDER = (
    "data_eligibility",
    "sample_size",
    "mechanical_dependency",
    "outlier_robustness",
    "statistical_significance",
    "hypothesis_generation",
    "recommendation_generation",
)

# Поля, которыми аналитический слой сообщил бы, что связь пережила удаление
# выбросов. Движок association-1.0.0 их не считает, и это не прячется:
# стадия 4 фиксирует «не проверено» и закрывает всё, что требует
# устойчивости. Гипотеза остаётся открытой — она и не утверждает
# устойчивости.
ROBUSTNESS_FIELDS = ("rho_no_outliers", "n_no_outliers")
ROBUSTNESS_NOT_ASSESSED = "robustness_not_assessed"
OUTLIER_DEPENDENT = "outlier_dependent"
ROBUSTNESS_DEPENDENT_TARGETS = ("FACT", "RECOMMENDATION", "content_dna_evidence")

NOT_MEASURED = "association_not_measured"
INSUFFICIENT_SAMPLE = "insufficient_sample"
NOISE = "not_distinguishable_from_noise"


def _stage(name, verdict, reason_code=None, detail=None):
    assert name in CHECK_ORDER, name
    assert verdict in ("pass", "restrict", "block"), verdict
    return {"stage": name, "verdict": verdict, "reason_code": reason_code,
            "detail": detail or {}}


class _Run:
    """Состояние одного прохода. Отдельный класс, чтобы стадии не могли
    менять порядок друг друга — они только добавляют записи и снимают
    разрешения."""

    def __init__(self, x_metric, y_metric):
        self.pair = P.canonical_pair(x_metric, y_metric)
        self.stages = []
        self.allow = {t: True for t in P.PROMOTION_TARGETS}
        self.deny = {}
        self.limitations = []
        self.stopped_at = None
        self.reason_code = None

    def _revoke(self, targets, stage, code, detail):
        for t in targets:
            self.allow[t] = False
            self.deny.setdefault(t, {"stage": stage, "reason_code": code,
                                     "detail": detail})

    def ok(self, name, detail=None):
        self.stages.append(_stage(name, "pass", detail=detail))

    def restrict(self, name, code, targets, detail=None, limitation=None):
        self.stages.append(_stage(name, "restrict", code, detail))
        self._revoke(targets, name, code, detail or {})
        if limitation:
            self.limitations.append(limitation)

    def stop(self, name, code, detail=None):
        self.stages.append(_stage(name, "block", code, detail))
        self._revoke(P.PROMOTION_TARGETS, name, code, detail or {})
        self.stopped_at = name
        self.reason_code = code

    def result(self):
        return {
            "pair": list(self.pair), "stages": self.stages,
            "executed": [s["stage"] for s in self.stages],
            "stopped_at": self.stopped_at, "reason_code": self.reason_code,
            "allow": dict(self.allow), "deny": dict(self.deny),
            "limitations": list(self.limitations),
            "check_order": list(CHECK_ORDER),
            "pipeline_version": PIPELINE_VERSION,
        }


def evaluate_association(a):
    """Провести одну измеренную связь по всем гейтам в утверждённом порядке."""
    x, y = a["x_metric"], a["y_metric"]
    r = _Run(x, y)

    # 1 ── data eligibility ─────────────────────────────────────────────
    if a.get("status") != "measured" or a.get("rho") is None:
        r.stop("data_eligibility", NOT_MEASURED,
               {"status": a.get("status"), "n": a.get("n"),
                "rho": a.get("rho")})
        return r.result()
    cov = a.get("coverage_ratio")
    r.ok("data_eligibility", {"status": a["status"], "coverage_ratio": cov})
    if cov is not None and cov < 1.0:
        r.limitations.append(
            f"связь посчитана на {100 * cov:.0f}% роликов: часть наблюдений "
            "выпала по пригодности, и подвыборка отобрана доступностью данных")

    # 2 ── sample size ──────────────────────────────────────────────────
    n = a.get("n") or 0
    if n < MIN_N_FOR_ASSOCIATION:
        r.stop("sample_size", INSUFFICIENT_SAMPLE,
               {"n": n, "min_n_for_association": MIN_N_FOR_ASSOCIATION})
        return r.result()
    if n < P.MIN_SAMPLE_FOR_FACT:
        r.restrict("sample_size", INSUFFICIENT_SAMPLE, ("FACT",),
                   {"n": n, "min_sample_required": P.MIN_SAMPLE_FOR_FACT,
                    "needed": P.MIN_SAMPLE_FOR_FACT - n},
                   f"n={n} ниже порога {P.MIN_SAMPLE_FOR_FACT} для вывода "
                   "уровня FACT")
    else:
        r.ok("sample_size", {"n": n,
                             "min_sample_required": P.MIN_SAMPLE_FOR_FACT})

    # 3 ── mechanical dependency ────────────────────────────────────────
    # Стоит ДО значимости намеренно: чем сильнее статистика у механически
    # зависимой пары, тем очевиднее, что измеряется арифметика метрик.
    dep = P.mechanical_dependency(x, y)
    if dep:
        r.stop("mechanical_dependency", P.MECHANICAL_REASON_CODE,
               {"rho": a["rho"], "p": a.get("p_two_sided"), "n": n,
                "rule_id": dep["rule_id"], "mechanism": dep["mechanism"],
                "evidence": dep["evidence"],
                "not_an_identity": dep["not_an_identity"],
                "forbidden": list(P.PROMOTION_TARGETS)})
        return r.result()
    cand = P.mechanical_candidate(x, y)
    if cand:
        denied = [t for t in P.PROMOTION_TARGETS
                  if t not in P.CANDIDATE_ALLOWED_TARGETS]
        r.restrict("mechanical_dependency", P.MECHANICAL_CANDIDATE_REASON_CODE,
                   denied,
                   {"rho": a["rho"], "p": a.get("p_two_sided"), "n": n,
                    "candidate_note": cand,
                    "allowed_targets": list(P.CANDIDATE_ALLOWED_TARGETS),
                    "forbidden": denied, "decision_required_from": "owner"},
                   "природа связи не решена владельцем: пара числится "
                   "кандидатом в механически зависимые")
    else:
        r.ok("mechanical_dependency", {"registered": False, "candidate": False})

    # 4 ── outlier / robustness ─────────────────────────────────────────
    missing = [f for f in ROBUSTNESS_FIELDS if a.get(f) is None]
    if missing:
        r.restrict("outlier_robustness", ROBUSTNESS_NOT_ASSESSED,
                   ROBUSTNESS_DEPENDENT_TARGETS,
                   {"missing_fields": missing,
                    "outlier_rule": A.OUTLIER_RULE_ID,
                    "engine_version": a.get("engine_version")},
                   "устойчивость связи к выбросам не проверена: движок связей "
                   "не пересчитывает коэффициент без выбросов")
    else:
        stable = (a["rho"] >= 0) == (a["rho_no_outliers"] >= 0)
        detail = {"rho": a["rho"], "rho_no_outliers": a["rho_no_outliers"],
                  "n_no_outliers": a["n_no_outliers"],
                  "outlier_rule": A.OUTLIER_RULE_ID, "sign_stable": stable}
        if stable:
            r.ok("outlier_robustness", detail)
        else:
            r.restrict("outlier_robustness", OUTLIER_DEPENDENT,
                       ROBUSTNESS_DEPENDENT_TARGETS, detail,
                       "знак связи меняется при удалении выбросов")

    # 5 ── statistical significance ─────────────────────────────────────
    p = a.get("p_two_sided")
    if p is None or p > P.HYPOTHESIS_MAX_P:
        r.stop("statistical_significance", NOISE,
               {"rho": a["rho"], "p": p, "n": n,
                "threshold": P.HYPOTHESIS_MAX_P})
        return r.result()
    r.ok("statistical_significance",
         {"p": p, "threshold": P.HYPOTHESIS_MAX_P,
          "method": a.get("significance_method")})

    # 6 ── hypothesis ───────────────────────────────────────────────────
    if r.allow["HYPOTHESIS"]:
        r.ok("hypothesis_generation", {"emitted": True})
    else:
        d = r.deny["HYPOTHESIS"]
        r.stages.append(_stage("hypothesis_generation", "block",
                               d["reason_code"], d["detail"]))

    # 7 ── recommendation ───────────────────────────────────────────────
    if r.allow["RECOMMENDATION"]:
        r.ok("recommendation_generation", {"emitted": True})
    else:
        d = r.deny["RECOMMENDATION"]
        r.stages.append(_stage("recommendation_generation", "block",
                               d["reason_code"], d["detail"]))
    return r.result()


def order_is_respected(executed):
    """Выполненные стадии обязаны быть подпоследовательностью CHECK_ORDER,
    идущей с начала и без перестановок."""
    return list(executed) == [s for s in CHECK_ORDER if s in set(executed)]
