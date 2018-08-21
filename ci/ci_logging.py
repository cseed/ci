import logging

log = logging.getLogger('ci')
log.setLevel(logging.INFO)
fmt = logging.Formatter('%(levelname)s:%(asctime)s:%(filename)s:%(funcName)s:%(lineno)d: %(message)s')

fh = logging.FileHandler('ci.log')
fh.setLevel(logging.INFO)
fh.setFormatter(fmt)
log.addHandler(fh)

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
log.addHandler(ch)
