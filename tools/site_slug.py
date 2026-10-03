"""Keep repository-authored GitHub heading links valid in the site."""
import re


def slugify(value, separator):
  value = re.sub(r'<[^>]*>', '', value).strip().lower()
  value = re.sub(r'[^\w\s-]', '', value)
  return re.sub(r'\s', separator, value)
