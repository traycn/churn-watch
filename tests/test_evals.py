from churnwatch.evals import Row, metrics
from churnwatch.llm import Result
from churnwatch.schema import Assessment


def _row(aid, label, pred, reason="usage_decline", pred_reason=None, ambiguous=False, tokens=(1000, 100)):
    a = None
    if pred is not None:
        a = Assessment(account_id=aid, at_risk=pred, risk_score=80 if pred else 10,
                       main_reason=(pred_reason or reason) if pred else "none", drivers=[], next_step="-")
    return Row(aid, label, reason if label else "none", ambiguous,
               Result(a, None if a else "err", tokens[0], tokens[1], 1500, False))


def test_metrics():
    rows = [
        _row("a", True, True),                                  # TP, reason right
        _row("b", True, True, pred_reason="billing"),           # TP, reason wrong
        _row("c", True, False, ambiguous=True),                 # FN
        _row("d", False, True, ambiguous=True),                 # FP
        _row("e", False, False),                                # TN
        _row("f", True, None),                                  # error -> counts as not flagged
    ]
    cfg = {"prices": {"m": {"input": 1.0, "output": 5.0}}}
    m = metrics(rows, "m", cfg)
    assert m["precision"] == round(2 / 3, 3)
    assert m["recall"] == 0.5
    assert m["accuracy"] == 0.5
    assert m["ambiguous_accuracy"] == 0.0
    assert m["reason_accuracy"] == 0.5
    assert m["errors"] == 1
    # 6 rows x (1000*1 + 100*5) / 1e6 = 0.009 USD -> per 1k accounts = 1.5
    assert m["cost_per_1k_accounts_usd"] == 1.5
    assert m["p50_latency_s"] == 1.5
