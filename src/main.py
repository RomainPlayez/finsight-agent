"""
main.py : interface en ligne de commande pour interroger l'agent
FinSight (donnees financieres structurees + analyse qualitative issue des
10-K, sur 8 entreprises).

Lancement (depuis la racine du projet) : python -m src.main

Tape 'quit' ou 'exit' pour quitter.
"""

from src.agent import run_agent, COMPANIES

def main() -> None:
    print("=== FinSight Agent ===")
    print(f"Entreprises couvertes : {', '.join(COMPANIES)}")
    print("Pose une question, ex. : \"Quel etait le revenu d'Apple en 2024 ?\"")
    print("                          \"Pourquoi le CA de Tesla a-t-il evolue ainsi ?\"")
    print("Tape 'quit' pour quitter.\n")

    history: list = []
    while True:
        try:
            user_input = input("Toi > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nA bientot !")
            break

        if user_input.lower() in ("quit", "exit"):
            print("A bientot !")
            break
        if not user_input:
            continue

        try:
            answer, history = run_agent(user_input, history=history)
        except Exception as exc:
            print(f"[Erreur] {exc}")
            continue

        print(f"\nAgent > {answer}\n")


if __name__ == "__main__":
    main()