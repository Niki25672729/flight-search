import argparse
import re
from datetime import datetime, timedelta

# Hardcoded list of European airports for validation
# In a real application, this might come from a configuration file or a database.
EU_AIRPORTS = {
    "EIN", "AMS", "LHR", "CDG", "MAD", "FCO", "BER", "DUB", "BRU", "VIE",
    "LIS", "ATH", "OSL", "CPH", "ARN", "HEL", "PRG", "WAW", "BUD", "BCN",
    "MXP", "MUC", "ZRH", "GVA", "VLC", "EDI", "GLA", "MAN", "LGW", "STN",
    "LTN", "DUS", "HAM", "STR", "CGN", "NCL", "BHD", "BFS", "ORK", "SNN",
    "BLQ", "CTA", "NAP", "PSA", "VCE", "VRN", "SXF", "TXL", "LEJ", "DRS",
    "NUE", "BRE", "HAJ", "DTM", "FMO", "LUB", "SXB", "NCE", "MRS", "BOD",
    "LYS", "TLS", "NTE", "RNS", "LIL", "BGY", "TSF", "AGP", "ALC", "PMI",
    "IBZ", "TFS", "LPA", "FNC", "OPO", "FAO", "KRK", "GDN", "WRO", "POZ",
    "KSC", "BTS", "KIV", "SOF", "VAR", "BOJ", "OTP", "CLJ", "TSR", "BEG",
    "ZAG", "SPU", "DBV", "TGD", "SKP", "SJJ", "LJU", "Tirana", "RIX", "VNO",
    "TLN", "KGD", "GOJ", "MSQ", "KBP", "ODS", "LWO", "HRK", "DNK", "DOK",
    "ROV", "KZN", "SVX", "UFA", "CEK", "OVB", "KJA", "IKT", "KJA", "VVO",
    "KGD", "LED", "AER", "ASF", "GRV", "MCX", "OGZ", "PEE", "NJC", "MMK",
    "KEJ", "OVB", "TJM", "VKO", "DME", "SVO", "GDZ", "ESB", "ADB", "SAW",
    "AYT", "DLM", "BJV", "ADA", "ASR", "DIY", "ERZ", "GZT", "HTY", "IST",
    "IZM", "KAY", "KYA", "MLX", "SSX", "TZX", "VAS", "VAN", "BAL", "BTZ",
    "EZS", "ERC", "KSY", "MZH", "SZF", "USQ", "DNZ", "NAK", "PQM", "GZP",
    "KCM", "AOE", "YEI", "SFG", "CIL", "KFS", "IGL", "ONQ", "KZR", "LTK",
    "JED", "DMM", "RUH", "MED", "DXB", "AUH", "SHJ", "DOH", "KWI", "BAH",
    "MCT", "AMM", "BEY", "DAM", "TLV", "CAI", "HRG", "SSH", "LXR", "ASW",
    "SSG", "FIH", "CKY", "NBO", "DAR", "ADD", "JNB", "CPT", "MRU", "SEZ",
    "CMN", "RAK", "AGA", "FEZ", "TNG", "ALG", "ORN", "TUN", "DJE", "MLA",
    "LCA", "PFO", "TLV", "AMM"
}

MAX_TIMERANGE_MONTHS = 3

def validate_departure_airport(airport: str) -> str:
    """
    Validates if the departure airport is a known European IATA code.
    """
    if airport.upper() not in EU_AIRPORTS:
        raise argparse.ArgumentTypeError(
            f"Invalid departure airport: {airport}. Must be a valid European IATA code."
        )
    return airport.upper()

def validate_timerange(timerange: str) -> str:
    """
    Validates the time range format (e.g., '3d', '2w', '1m') and ensures it's within 3 months.
    """
    match = re.fullmatch(r"(\d+)([dwm])", timerange)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid TIMERANGE format: {timerange}. Expected format: <number>[d|w|m] (e.g., 3d, 2w, 1m)."
        )

    value = int(match.group(1))
    unit = match.group(2)

    if value <= 0:
        raise argparse.ArgumentTypeError("TIMERANGE value must be a positive number.")

    # Convert to days for comparison
    if unit == 'd':
        total_days = value
    elif unit == 'w':
        total_days = value * 7
    elif unit == 'm':
        # Approximate 1 month as 30 days for maximum range check
        total_days = value * 30

    if total_days > MAX_TIMERANGE_MONTHS * 30: # 3 months * 30 days/month
        raise argparse.ArgumentTypeError(
            f"TIMERANGE exceeds maximum allowed duration of {MAX_TIMERANGE_MONTHS} months."
        )

    return timerange

def validate_budget(budget_str: str) -> int:
    """
    Validates if the budget is a positive integer.
    """
    try:
        budget = int(budget_str)
        if budget <= 0:
            raise argparse.ArgumentTypeError(
                f"Invalid BUDGET: {budget_str}. Must be a positive integer."
            )
        return budget
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid BUDGET format: {budget_str}. Must be an integer."
        )

def parse_arguments() -> argparse.Namespace:
    """
    Parses CLI arguments for flight search.
    """
    parser = argparse.ArgumentParser(
        description="Search for budget flights from a European departure airport within a given time range and budget."
    )

    parser.add_argument(
        "DEPARTURE_AIRPORT",
        nargs="?", # Makes the argument optional
        default="EIN",
        type=validate_departure_airport,
        help="Departure airport IATA code (EU only). Default: EIN"
    )
    parser.add_argument(
        "TIMERANGE",
        nargs="?", # Makes the argument optional
        default="1m",
        type=validate_timerange,
        help="Time range for the search (e.g., 3d, 2w, 1m). Max: 3 months. Default: 1m"
    )
    parser.add_argument(
        "BUDGET",
        nargs="?", # Makes the argument optional
        default="50",
        type=validate_budget,
        help="Maximum budget in euros. Must be a positive integer. Default: 50"
    )

    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()
    print(f"Departure Airport: {args.DEPARTURE_AIRPORT}")
    print(f"Time Range: {args.TIMERANGE}")
    print(f"Budget: {args.BUDGET}")
