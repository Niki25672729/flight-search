import unittest
from unittest.mock import patch
import argparse
from src.cli import (
    validate_departure_airport,
    validate_timerange,
    validate_budget,
    parse_arguments,
    EU_AIRPORTS,
    MAX_TIMERANGE_MONTHS
)

class TestCliFunctions(unittest.TestCase):

    # --- Test validate_departure_airport ---
    def test_validate_departure_airport_valid(self):
        self.assertEqual(validate_departure_airport("EIN"), "EIN")
        self.assertEqual(validate_departure_airport("LHR"), "LHR")
        self.assertEqual(validate_departure_airport("cdg"), "CDG") # Test case insensitivity

    def test_validate_departure_airport_invalid_iata(self):
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid departure airport: XYZ"):
            validate_departure_airport("XYZ")

    def test_validate_departure_airport_non_european(self):
        # Assuming JFK is not in EU_AIRPORTS
        if "JFK" in EU_AIRPORTS:
            # Temporarily remove JFK if it somehow got in (for robust testing)
            original_jfk_state = True
            EU_AIRPORTS.remove("JFK")
        else:
            original_jfk_state = False

        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid departure airport: JFK"):
            validate_departure_airport("JFK")

        # Restore original state if modified
        if original_jfk_state:
            EU_AIRPORTS.add("JFK")

    # --- Test validate_timerange ---
    def test_validate_timerange_valid_days(self):
        self.assertEqual(validate_timerange("1d"), "1d")
        self.assertEqual(validate_timerange("30d"), "30d")

    def test_validate_timerange_valid_weeks(self):
        self.assertEqual(validate_timerange("1w"), "1w")
        self.assertEqual(validate_timerange("12w"), "12w") # Approx 3 months

    def test_validate_timerange_valid_months(self):
        self.assertEqual(validate_timerange("1m"), "1m")
        self.assertEqual(validate_timerange(f"{MAX_TIMERANGE_MONTHS}m"), f"{MAX_TIMERANGE_MONTHS}m")

    def test_validate_timerange_invalid_format(self):
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid TIMERANGE format"):
            validate_timerange("3")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid TIMERANGE format"):
            validate_timerange("3x")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid TIMERANGE format"):
            validate_timerange("d3")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid TIMERANGE format"):
            validate_timerange("1.5m")

    def test_validate_timerange_zero_value(self):
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "TIMERANGE value must be a positive number."):
            validate_timerange("0d")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "TIMERANGE value must be a positive number."):
            validate_timerange("0w")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "TIMERANGE value must be a positive number."):
            validate_timerange("0m")

    def test_validate_timerange_negative_value(self):
        # Regex won't match negative numbers as \d+ expects one or more digits
        # So it will fail on format before reaching the value check.
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid TIMERANGE format"):
            validate_timerange("-1d")

    def test_validate_timerange_exceeds_max_months(self):
        # Test just over the max for each unit
        with self.assertRaisesRegex(argparse.ArgumentTypeError, f"TIMERANGE exceeds maximum allowed duration of {MAX_TIMERANGE_MONTHS} months."):
            validate_timerange(f"{MAX_TIMERANGE_MONTHS * 30 + 1}d") # 3 months + 1 day
        with self.assertRaisesRegex(argparse.ArgumentTypeError, f"TIMERANGE exceeds maximum allowed duration of {MAX_TIMERANGE_MONTHS} months."):
            validate_timerange(f"{MAX_TIMERANGE_MONTHS * 4 + 1}w") # Approx 3 months + 1 week
        with self.assertRaisesRegex(argparse.ArgumentTypeError, f"TIMERANGE exceeds maximum allowed duration of {MAX_TIMERANGE_MONTHS} months."):
            validate_timerange(f"{MAX_TIMERANGE_MONTHS + 1}m")

    # --- Test validate_budget ---
    def test_validate_budget_valid(self):
        self.assertEqual(validate_budget("1"), 1)
        self.assertEqual(validate_budget("100"), 100)
        self.assertEqual(validate_budget("5000"), 5000)

    def test_validate_budget_invalid_format(self):
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid BUDGET format: abc"):
            validate_budget("abc")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid BUDGET format: 1.5"):
            validate_budget("1.5")
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid BUDGET format: "):
            validate_budget("")

    def test_validate_budget_zero(self):
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid BUDGET: 0. Must be a positive integer."):
            validate_budget("0")

    def test_validate_budget_negative(self):
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "Invalid BUDGET: -10. Must be a positive integer."):
            validate_budget("-10")

class TestParseArguments(unittest.TestCase):

    # Patch sys.exit to prevent the program from exiting during tests
    # and instead raise SystemExit
    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_defaults(self, mock_exit):
        # Simulate no arguments being passed
        with patch('sys.argv', ['flight_search.py']):
            args = parse_arguments()
            self.assertEqual(args.DEPARTURE_AIRPORT, "EIN")
            self.assertEqual(args.TIMERANGE, "1m")
            self.assertEqual(args.BUDGET, 50)

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_all_valid(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'AMS', '2w', '150']):
            args = parse_arguments()
            self.assertEqual(args.DEPARTURE_AIRPORT, "AMS")
            self.assertEqual(args.TIMERANGE, "2w")
            self.assertEqual(args.BUDGET, 150)

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_partial_valid(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'LHR']):
            args = parse_arguments()
            self.assertEqual(args.DEPARTURE_AIRPORT, "LHR")
            self.assertEqual(args.TIMERANGE, "1m") # Default
            self.assertEqual(args.BUDGET, 50) # Default

        with patch('sys.argv', ['flight_search.py', 'CDG', '3w']):
            args = parse_arguments()
            self.assertEqual(args.DEPARTURE_AIRPORT, "CDG")
            self.assertEqual(args.TIMERANGE, "3w")
            self.assertEqual(args.BUDGET, 50) # Default

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_invalid_departure_airport(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'JFK']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_invalid_timerange_format(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'EIN', '2x']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_invalid_timerange_value(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'EIN', '0m']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_timerange_exceeds_max(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'EIN', f"{MAX_TIMERANGE_MONTHS + 1}m"]):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_invalid_budget_format(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'EIN', '1m', 'abc']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_invalid_budget_value(self, mock_exit):
        with patch('sys.argv', ['flight_search.py', 'EIN', '1m', '-50']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()
        mock_exit.reset_mock() # Reset mock for next test if needed

        with patch('sys.argv', ['flight_search.py', 'EIN', '1m', '0']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

    @patch('argparse.ArgumentParser.exit', side_effect=SystemExit)
    def test_parse_arguments_mixed_valid_invalid(self, mock_exit):
        # Invalid airport, others valid but shouldn't be reached after validation error
        with patch('sys.argv', ['flight_search.py', 'LAX', '2m', '100']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()
        mock_exit.reset_mock()

        # Valid airport, invalid timerange, valid budget (budget won't be validated)
        with patch('sys.argv', ['flight_search.py', 'AMS', 'invalid_time', '100']):
            with self.assertRaises(SystemExit):
                parse_arguments()
            mock_exit.assert_called_once()

if __name__ == '__main__':
    unittest.main()
