"""
eval_agent.py : evalue l'agent FinSight sur une batterie de questions a
reponse connue (chiffres verifies dans la base SQL constituee depuis les
10-K SEC EDGAR, et faits verifies dans l'index RAG).

Lancement (depuis la racine du projet, avec le venv active) :
    python eval_agent.py

Produit :
    - docs/eval_results.json detail complet, question par question)
    - un resume affiche dans la console (taux de bonnes reponses, erreurs)

Chaque question est posee independamment (nouvelle conversation a chaque
fois, pas d'historique partage) pour evaluer l'agent question par question.
"""

import json
import re
import time
import unicodedata

from src.agent import run_agent
from src.db import get_connection, get_metric

TOLERANCE = 0.005  # 0.5% de tolerance sur les valeurs numeriques

UNAVAILABLE_PATTERNS = [
    "pas disponible", "aucune donnee", "ne dispose pas", "n'est pas disponible",
    "pas connu", "indisponible", "pas de donnee", "non disponible",
    "pas trouve", "peux pas inventer", "ne peux pas inventer",
]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def extract_numbers(text: str):
    normalized = text.replace(" ", " ").replace(" ", " ")
    candidates = re.findall(r"-?\d[\d\s,\.]*\d|-?\d", normalized)
    numbers = []
    for c in candidates:
        cleaned = c.replace(" ", "").replace(",", "")
        try:
            numbers.append(float(cleaned))
        except ValueError:
            continue
    return numbers


def has_unavailable_phrase(answer: str) -> bool:
    a = strip_accents(answer)
    return any(strip_accents(p) in a for p in UNAVAILABLE_PATTERNS)


def numeric_match(answer: str, expected: float) -> bool:
    numbers = extract_numbers(answer)
    target = abs(expected)
    found_magnitude = any(
        target > 0 and abs(abs(n) - target) / target <= TOLERANCE
        for n in numbers
    )
    if not found_magnitude:
        return False
    if expected < 0:
        a = strip_accents(answer)
        has_minus = any(n < 0 for n in numbers)
        has_loss_word = any(w in a for w in ["perte", "deficit", "negatif", "negative"])
        return has_minus or has_loss_word
    return True


def keyword_match(answer: str, keywords) -> bool:
    a = strip_accents(answer)
    return any(strip_accents(k) in a for k in keywords)


# Batterie de questions
# type "numeric" : question chiffree
# type "trap" : donnee volontairement absente (hors couverture)
#     -> l'agent doit dire que la donnee n'est pas disponible
# type "qualitative" : question narrative (RAG)
#     -> reellement present dans le MD&A indexe
# type "combined"  : chiffre(s) + explication, les deux sont verifies

QUESTIONS = [
    {"id": 1, "type": "numeric", "q": "Quel etait le resultat net d'Apple en 2024 ?", "ticker": "AAPL", "fy": 2024, "metric": "NetIncomeLoss"},
    {"id": 2, "type": "numeric", "q": "Quel etait le resultat net de Microsoft en 2024 ?", "ticker": "MSFT", "fy": 2024, "metric": "NetIncomeLoss"},
    {"id": 3, "type": "numeric", "q": "Quel etait le resultat net d'Amazon en 2022 ?", "ticker": "AMZN", "fy": 2022, "metric": "NetIncomeLoss"},
    {"id": 4, "type": "numeric", "q": "Quel etait le flux de tresorerie operationnel de Goldman Sachs en 2023 ?", "ticker": "GS", "fy": 2023, "metric": "OperatingCashFlow"},
    {"id": 5, "type": "numeric", "q": "Quel etait le chiffre d'affaires de JPMorgan en 2025 ?", "ticker": "JPM", "fy": 2025, "metric": "Revenues"},
    {"id": 6, "type": "numeric", "q": "Quel etait le chiffre d'affaires de NVIDIA en 2026 ?", "ticker": "NVDA", "fy": 2026, "metric": "Revenues"},
    {"id": 7, "type": "numeric", "q": "Quels etaient les actifs totaux de Tesla en 2023 ?", "ticker": "TSLA", "fy": 2023, "metric": "Assets"},
    {"id": 8, "type": "numeric", "q": "Quels etaient les passifs totaux de Meta en 2024 ?", "ticker": "META", "fy": 2024, "metric": "Liabilities"},
    {"id": 9, "type": "numeric", "q": "Quel etait le chiffre d'affaires d'Apple en 2021 ?", "ticker": "AAPL", "fy": 2021, "metric": "Revenues"},
    {"id": 10, "type": "numeric", "q": "Quels etaient les actifs totaux de Microsoft en 2026 ?", "ticker": "MSFT", "fy": 2026, "metric": "Assets"},
    {"id": 11, "type": "numeric", "q": "Quel etait le resultat net de NVIDIA en 2021 ?", "ticker": "NVDA", "fy": 2021, "metric": "NetIncomeLoss"},
    {"id": 12, "type": "numeric", "q": "Quel etait le chiffre d'affaires de Goldman Sachs en 2022 ?", "ticker": "GS", "fy": 2022, "metric": "Revenues"},
    {"id": 13, "type": "numeric", "q": "Quels etaient les passifs totaux de JPMorgan en 2021 ?", "ticker": "JPM", "fy": 2021, "metric": "Liabilities"},
    {"id": 14, "type": "numeric", "q": "Quel etait le resultat net de Tesla en 2025 ?", "ticker": "TSLA", "fy": 2025, "metric": "NetIncomeLoss"},
    {"id": 15, "type": "trap", "q": "Quel etait le chiffre d'affaires de Tesla en 2026 ?", "ticker": "TSLA", "fy": 2026, "metric": "Revenues"},
    {"id": 16, "type": "trap", "q": "Quel etait le resultat net d'Amazon en 2020 ?", "ticker": "AMZN", "fy": 2020, "metric": "NetIncomeLoss"},
    {"id": 17, "type": "qualitative", "q": "Pourquoi NVIDIA investit-il autant dans les data centers ?", "keywords": ["data center"]},
    {"id": 18, "type": "qualitative", "q": "Quel risque NVIDIA mentionne-t-il concernant la concentration de ses clients ?", "keywords": ["concentration", "clients directs", "dependance"]},
    {"id": 19, "type": "combined", "q": "Comment le chiffre d'affaires de Tesla a-t-il evolue entre 2023 et 2025, et pourquoi ?", "ticker": "TSLA", "metric": "Revenues", "fys": [2023, 2025], "keywords": ["demande", "concurrence", "prix", "livraison"]},
    {"id": 20, "type": "combined", "q": "Quel est le resultat net de Microsoft en 2024, et quels facteurs expliquent la croissance de son activite cloud ?", "ticker": "MSFT", "fy": 2024, "metric": "NetIncomeLoss", "keywords": ["cloud", "azure", "intelligence artificielle"]},
]


def grade(item, answer, conn):
    if item["type"] == "numeric":
        row = get_metric(conn, item["ticker"], item["fy"], item["metric"])
        expected = row[0] if row else None
        ok = numeric_match(answer, expected) if expected is not None else False
        return ok, f"attendu {expected}"

    if item["type"] == "trap":
        row = get_metric(conn, item["ticker"], item["fy"], item["metric"])
        assert row is None, f"donnee censee etre absente mais trouvee en base : {item}"
        ok = has_unavailable_phrase(answer)
        return ok, "attendu : signaler l'absence de donnee"

    if item["type"] == "qualitative":
        ok = keyword_match(answer, item["keywords"])
        return ok, f"mot-cle attendu parmi {item['keywords']}"

    if item["type"] == "combined":
        if "fys" in item:
            expecteds = [get_metric(conn, item["ticker"], fy, item["metric"])[0] for fy in item["fys"]]
            nums_ok = all(numeric_match(answer, e) for e in expecteds)
            detail = f"attendus {dict(zip(item['fys'], expecteds))}"
        else:
            row = get_metric(conn, item["ticker"], item["fy"], item["metric"])
            expected = row[0]
            nums_ok = numeric_match(answer, expected)
            detail = f"attendu {expected}"
        kw_ok = keyword_match(answer, item["keywords"])
        ok = nums_ok and kw_ok
        return ok, f"{detail} ; mot-cle attendu parmi {item['keywords']} ; chiffres_ok={nums_ok} mot_cle_ok={kw_ok}"

    raise ValueError(f"type inconnu: {item['type']}")


def main():
    conn = get_connection()
    results = []
    n_correct = 0

    for item in QUESTIONS:
        print(f"[{item['id']:>2}/{len(QUESTIONS)}] {item['q']}")
        t0 = time.time()
        try:
            answer, _ = run_agent(item["q"], history=None)
            elapsed = time.time() - t0
            ok, detail = grade(item, answer, conn)
        except Exception as exc:
            elapsed = time.time() - t0
            answer = f"[ERREUR] {exc}"
            ok, detail = False, "exception levee pendant l'appel"

        n_correct += int(ok)
        results.append({
            "id": item["id"],
            "type": item["type"],
            "question": item["q"],
            "answer": answer,
            "correct": ok,
            "detail": detail,
            "elapsed_s": round(elapsed, 1),
        })
        print(f"    -> {'OK' if ok else 'ECHEC'} ({elapsed:.1f}s)")

    conn.close()

    accuracy = 100 * n_correct / len(QUESTIONS)
    summary = {
        "total": len(QUESTIONS),
        "correct": n_correct,
        "accuracy_pct": round(accuracy, 1),
        "results": results,
    }

    with open("docs/eval_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print()
    print(f"=== RESULTAT : {n_correct}/{len(QUESTIONS)} ({accuracy:.1f}%) ===")
    print("Echecs :")
    for r in results:
        if not r["correct"]:
            print(f"  - [{r['id']}] {r['question']}")
            print(f"      reponse agent : {r['answer'][:300]}")
            print(f"      {r['detail']}")


if __name__ == "__main__":
    main()