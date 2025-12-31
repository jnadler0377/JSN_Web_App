from pathlib import Path
def ensure_case_folder(root: str, case_number: str) -> str:
    safe = case_number.replace('/', '-').replace('\\', '-').replace(' ', '_')
    d = Path(root) / safe
    d.mkdir(parents=True, exist_ok=True)
    return str(d)
def compute_offer_70(arv: float, rehab: float, closing: float) -> float:
    try:
        return max(0.0, (float(arv) * 0.65) - float(rehab) - float(closing))
    except Exception:
        return 0.0

def compute_offer_80(arv: float, rehab: float, closing: float) -> float:
    try:
        rate = 0.85 if float(arv) > 350000 else 0.80
        return max(0.0, (float(arv) * rate) - float(rehab) - float(closing))
    except Exception:
        return 0.0
