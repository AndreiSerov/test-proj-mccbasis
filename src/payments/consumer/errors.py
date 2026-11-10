class Unprocessable(Exception):
    """A message retrying cannot fix (malformed body, unknown payment_id)."""
