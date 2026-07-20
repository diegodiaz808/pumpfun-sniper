def evaluate_token(token):
    score = 50

    # Actividad mínima
    if token["trades"] >= 8:
        score += 5

    if token["trades"] >= 15:
        score += 10

    # Volumen observado en SOL
    if token["volume"] >= 1.5:
        score += 5

    if token["volume"] >= 3:
        score += 10

    # Presión compradora
    if token["buys"] > token["sells"]:
        score += 10

    if token["buy_volume"] > token["sell_volume"]:
        score += 10

    # Si no hubo casi trades, mala señal
    if token["trades"] < 3:
        score -= 10

    passed = score >= 70

    return {
        "passed": passed,
        "score": score
    }