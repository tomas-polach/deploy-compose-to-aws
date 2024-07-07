import unicodedata
import re


def to_pascal_case(input_string: str) -> str:
    # Normalize Unicode characters to their closest ASCII equivalent
    normalized_str = unicodedata.normalize('NFKD', input_string).encode('ascii', 'ignore').decode('ascii')
    # Replace non-alphanumeric characters (including underscores and hyphens) with spaces
    normalized_str = re.sub(r'[^a-zA-Z0-9]+', ' ', normalized_str)
    # Split the string into words based on spaces
    words = normalized_str.split()
    # Capitalize the first letter of each word and join them
    # Check if the input string is already in camelCase or PascalCase
    if len(words) == 1 and (words[0][0].islower() or words[0][0].isupper() and any(c.islower() for c in words[0])):
        return words[0]
    pascal_case_str = ''.join(word.capitalize() for word in words)
    return pascal_case_str
