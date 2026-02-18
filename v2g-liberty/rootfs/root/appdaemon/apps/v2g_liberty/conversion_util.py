def parse_to_int(number_string, default_value: int):
    """Reliably parse a string, float or int to an int. If un-parsable return the default value.
    :param number_string: str, float, int, bool (not dict or list)
    :param default_value: int that is returned if parsing failed.
    :return: parsed int
    """
    try:
        return int(float(number_string))
    except:
        return default_value