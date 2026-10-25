"""
test_rag.py -- verifie que la recherche semantique dans l'index Chroma
fonctionne correctement avant de construire l'agent par-dessus.
A lancer depuis la racine du projet : python -m test_rag
(ou python test_rag.py si tu le preferes -- il ne depend pas de src en
import relatif, donc les deux marchent ici).
A supprimer une fois la verification faite (ce n'est pas un module du
pipeline, juste un test ponctuel).
"""

from src.rag import rag_search


def show_results(title: str, results: list) -> None:
    print(f"\n=== {title} ===")
    if not results:
        print("  Aucun resultat -- probleme a investiguer.")
        return
    for i, r in enumerate(results):
        snippet = r["text"][:200].replace("\n", " ")
        print(f"  [{i}] {r['ticker']} (distance={r['distance']:.4f})")
        print(f"      {snippet}...")


if __name__ == "__main__":
    # Recherche filtree sur une entreprise precise.
    show_results(
        "Tesla : croissance des livraisons de vehicules",
        rag_search("vehicle deliveries growth production", ticker="TSLA", top_k=3),
    )

    # Recherche filtree sur une autre entreprise.
    show_results(
        "JPMorgan : gestion du risque de credit",
        rag_search("credit risk management loan losses", ticker="JPM", top_k=3),
    )

    # Recherche sans filtre -- doit remonter l'entreprise la plus pertinente
    # parmi les 8, sans qu'on lui dise laquelle chercher.
    show_results(
        "Sans filtre : intelligence artificielle et data centers",
        rag_search("artificial intelligence data center investment", top_k=5),
    )
