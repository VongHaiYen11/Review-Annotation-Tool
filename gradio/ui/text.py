"""Display annotation markers without changing the stored text."""
import re


def display_annotation_text(text):
    return re.sub(r'(?:MISS)+',
                  lambda match: ' '.join(['<miss>'] * (len(match.group()) // 4)),
                  text)
