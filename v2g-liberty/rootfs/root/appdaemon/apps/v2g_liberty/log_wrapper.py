import inspect


def get_class_method_logger(ad_log):
    def log(msg: str, level: str = ""):
        info = inspect.stack()[1][0]
        the_class = __truncate(info.f_locals["self"].__class__.__name__, 20)
        the_method = info.f_code.co_name
        # The current custom is to add the method name to the message, this is not needed any more as
        # it is added here already. So, if it is in the message, remove it.
        msg = msg.replace(f"{the_method} ", "")
        the_method = __truncate(the_method, 20)
        line_no = info.f_lineno
        if level == "":
            level = "INFO"
        msg = f"{the_class}.{line_no} > {the_method} > {msg}"
        ad_log(msg=msg, level=level)

    def __truncate(the_string:str, length:int):
        if len(the_string) > length:
            length_end = int(length/2)
            length = length - 1 - length_end
            the_string = the_string[:length] + "~" + the_string[-length_end:]
        return the_string

    return log
