import json
import re


COURSE_RE = re.compile(r"\b([A-Z]{2,}(?:\s*/\s*[A-Z]{2,})?)\s*(\d{3,4}[A-Z]?)\b")
REQUISITE_WORD_RE = re.compile(
    r"\b(?:pre(?:-| )?requisites?|prerequisites?|co(?:-| )?requisites?|corequisites?|"
    r"anti(?:-| )?requisites?|antirequisites?)\b",
    re.IGNORECASE,
)
MARKER_RE = re.compile(
    r"(?i)\b("
    r"pre\s*-\s*or\s*co\s*-\s*re(?:qui|qu)site?s?|"
    r"pre\s+or\s+co\s*-\s*re(?:qui|qu)site?s?|"
    r"pre\s*/\s*co\s*re(?:qui|qu)site?s?|"
    r"pre(?:-|\s+)?re(?:qui|qu)site?s?|"
    r"co(?:-|\s+)?re(?:qui|qu)site?s?|core(?:qui|qu)site?s?|"
    r"anti\s*-\s*re(?:qui|qu)site?s?|antire(?:qui|qu)site?s?|"
    r"AREQ|areq"
    r")\s*:"
)

SPECIAL_PATTERNS = [
    (re.compile(r"(?i)\bconsent\s+of\s+(?:the\s+)?instructor\b"), "consent_of_instructor"),
    (re.compile(r"(?i)\binstructor\s+consent\b"), "consent_of_instructor"),
    (re.compile(r"(?i)\binstructor\s+permission\b"), "consent_of_instructor"),
    (re.compile(r"(?i)\binstructor\s+approval\b"), "consent_of_instructor"),
    (re.compile(r"(?i)\bpermission\s+of\s+(?:the\s+)?instructor\b"), "consent_of_instructor"),
    (re.compile(r"(?i)\bapproval\s+of\s+(?:the\s+)?instructor\b"), "consent_of_instructor"),
    (re.compile(r"(?i)\bplacement(?:\s+test)?\b"), "placement"),
    (re.compile(r"(?i)\bequivalent\b"), "equivalent_course_or_background"),
    (re.compile(r"(?i)\b(sophomore|junior|senior|graduate)\s+standing\s+or\s+above\b"), r"\1_standing_or_above"),
    (re.compile(r"(?i)\bjunior\s+standing\b"), "junior_standing"),
    (re.compile(r"(?i)\bsenior\s+standing\b"), "senior_standing"),
    (re.compile(r"(?i)\bsophomore\s+standing\b"), "sophomore_standing"),
    (re.compile(r"(?i)\bgraduate\s+standing\b"), "graduate_standing"),
]

IGNORED_WORDS = {
    "a", "an", "and", "above", "better", "by", "consent", "course", "courses",
    "equivalent", "for", "grade", "in", "instructor", "instructors", "minimum",
    "no", "of", "or", "permission", "placement", "standing", "student", "students",
    "test", "the", "to", "with",
}

RECOMMEND_RE = re.compile(
    r"\s*(?:,?\s+and\s+)?(?:is|are|highly|strongly)\s+(?:recommended|encouraged)\s*[.;]?",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text)


def normalize_course(subject: str, number: str) -> str:
    subject = re.sub(r"\s+", "", subject.upper())
    return f"{subject} {number.upper()}"


def make_node(kind, items=None, **extra):
    result = {"type": kind}
    if items is not None:
        result["items"] = items
    result.update(extra)
    return result


def simplify(tree):
    if not isinstance(tree, dict):
        return tree
    if tree.get("type") in {"and", "or"}:
        items = []
        for item in tree.get("items", []):
            item = simplify(item)
            if isinstance(item, dict) and item.get("type") == tree["type"]:
                items.extend(item.get("items", []))
            elif item:
                items.append(item)
        if len(items) == 1:
            return items[0]
        return make_node(tree["type"], items)
    return tree


def extract_specials(text: str):
    GRADE = {"sophomore": 2, "junior": 3, "senior": 4, "graduate": 5}
    SKIP_CONDITIONS = {"consent_of_instructor", "instructor_approval"}
    specials = []
    has_standing_or_above = False
    for pattern, label in SPECIAL_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                expanded = match.expand(label)
            except (re.error, IndexError):
                expanded = label
            for level, num in GRADE.items():
                if expanded.lower() == f"{level}_standing":
                    expanded = f"standing:{num}"
                    break
                if expanded.lower() == f"{level}_standing_or_above":
                    expanded = f"standing_above:{num}"
                    break
            if "standing_above" in expanded:
                has_standing_or_above = True
            elif has_standing_or_above and expanded.startswith("standing:"):
                continue
            if expanded in SKIP_CONDITIONS:
                continue
            if expanded.startswith("standing") or expanded.startswith("standing_"):
                continue
            specials.append(expanded)
    return list(dict.fromkeys(specials))


def special_label(text: str) -> str:
    text = text.strip().lower()
    for pattern, label in SPECIAL_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            try:
                expanded = match.expand(label)
            except (re.error, IndexError):
                expanded = label
            return expanded
    return text.replace(" ", "_")


def marker_kind(marker: str) -> str:
    compact = re.sub(r"[\s\-/]+", "", marker.lower())
    if compact.startswith("preorco") or compact.startswith("preco"):
        return "pre_or_corequisite"
    if compact.startswith("anti") or compact.startswith("areq"):
        return "antirequisite"
    if compact.startswith("co"):
        return "corequisite"
    return "prerequisite"


def split_sections(text: str):
    matches = list(MARKER_RE.finditer(text))
    if not matches:
        return [("unlabeled", text)]
    sections = []
    if matches[0].start() > 0:
        sections.append(("unlabeled", text[:matches[0].start()].strip()))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((marker_kind(match.group(1)), text[start:end].strip()))
    return sections


def expand_slash_alternatives(text: str) -> str:
    return re.sub(
        r"\b([A-Z]{2,})\s*(\d{3,4}[A-Z]?)\s*/\s*([A-Z]{2,})\s*(\d{3,4}[A-Z]?)",
        lambda m: f"{m.group(1)} {m.group(2)} or {m.group(3)} {m.group(4)}",
        text,
        flags=re.IGNORECASE,
    )


def expand_same_subject_options(text: str) -> str:
    pattern = re.compile(r"\b([A-Z]{2,})\s+(\d{3,4}[A-Z]?)(\s+or\s+\d{3,4}[A-Z]?)+\b", re.IGNORECASE)

    def repl(match):
        subject = match.group(1).upper()
        numbers = re.findall(r"\d{3,4}[A-Z]?", match.group(0), flags=re.IGNORECASE)
        courses = [f"{subject} {number.upper()}" for number in numbers]
        return "(" + " or ".join(courses) + ")"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def tokenize(text: str):
    token_re = re.compile(
        r"(?P<placement>\b(?:(?:[A-Z][a-z]+(?:\s+(?:of|the|for|to|in))?)\s+)+[Pp]lacement(?:\s+[Tt]est)?\b)|"
        r"(?P<special>\b(?:consent\s+of\s+(?:the\s+)?instructor|instructor\s+consent|instructor\s+permission|instructor\s+approval|"
        r"permission\s+of\s+(?:the\s+)?instructor|approval\s+of\s+(?:the\s+)?instructor|"
        r"placement(?:\s+test)?|equivalent|"
        r"(?:sophomore|junior|senior|graduate)\s+standing(?:\s+or\s+above)?)\b)|"
        r"(?P<course>\b(?!(?:and|or)\b)[A-Z][A-Za-z]+(?:\s*/\s*[A-Z][A-Za-z]+)?\s*\d{3,4}[A-Z]?\b)|"
        r"(?P<number>\b\d{3,4}[A-Z]?\b)|"
        r"(?P<and>\band\b|&)|"
        r"(?P<or>\bor\b)|"
        r"(?P<lparen>\()|(?P<rparen>\))|"
        r"(?P<comma>,)|"
        r"(?P<word>[A-Za-z][A-Za-z'-]*)",
        re.IGNORECASE,
    )
    tokens = []
    for match in token_re.finditer(text):
        kind = match.lastgroup
        raw = match.group(0)
        if kind == "placement":
            tokens.append(("SPECIAL", raw.strip()))
        elif kind == "special":
            tokens.append(("SPECIAL", special_label(raw)))
        elif kind == "course":
            course_match = COURSE_RE.search(raw.upper())
            tokens.append(("COURSE", normalize_course(course_match.group(1), course_match.group(2))))
        elif kind == "number":
            tokens.append(("NUMBER", raw.upper()))
        elif kind == "and":
            tokens.append(("AND", raw))
        elif kind == "or":
            tokens.append(("OR", raw))
        elif kind == "comma":
            tokens.append(("AND", raw))
        elif kind == "lparen":
            tokens.append(("LPAREN", raw))
        elif kind == "rparen":
            tokens.append(("RPAREN", raw))
        elif kind == "word":
            tokens.append(("WORD", raw))
    filtered = []
    for kind, value in tokens:
        if kind in {"AND", "OR"}:
            if filtered and filtered[-1][0] in {"AND", "OR"}:
                filtered.pop()
        filtered.append((kind, value))
    return filtered


class ExpressionParser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.last_subject = ""
        self.unknown_words = []

    def peek(self):
        if self.pos >= len(self.tokens):
            return ("EOF", "")
        return self.tokens[self.pos]

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def parse(self):
        result = self.parse_or()
        while self.peek()[0] not in {"EOF", "WORD"}:
            kind = self.peek()[0]
            if kind in {"COURSE", "LPAREN", "SPECIAL", "NUMBER"}:
                right = self.parse_or()
                if right:
                    result = make_node("and", [result, right]) if result else right
            else:
                self.take()
        while self.peek()[0] != "EOF":
            kind, value = self.take()
            if kind == "WORD":
                self.unknown_words.append(value)
        return simplify(result) if result else None

    def parse_or(self):
        items = [self.parse_and()]
        while self.peek()[0] == "OR":
            self.take()
            items.append(self.parse_and())
        items = [item for item in items if item]
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        return make_node("or", items)

    def parse_and(self):
        items = [self.parse_primary()]
        while self.peek()[0] == "AND":
            self.take()
            items.append(self.parse_primary())
        items = [item for item in items if item]
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        return make_node("and", items)

    def parse_primary(self):
        while self.pos < len(self.tokens):
            kind, value = self.peek()
            if kind == "COURSE":
                self.take()
                self.last_subject = value.split(" ", 1)[0]
                node = make_node("course", code=value)
                if self.peek()[0] == "LPAREN":
                    paren_parts = []
                    self.take()
                    while self.pos < len(self.tokens) and self.peek()[0] != "RPAREN":
                        paren_parts.append(self.take()[1])
                    if self.peek()[0] == "RPAREN":
                        self.take()
                    if paren_parts:
                        node["notation"] = " ".join(paren_parts)
                return node
            if kind == "SPECIAL":
                self.take()
                cond = special_label(value)
                if cond == "placement":
                    if value.lower() != "placement":
                        return make_node("special", condition="placement", text=value)
                    return make_node("special", condition="placement")
                if "placement" in value.lower():
                    return make_node("special", condition="placement", text=value)
                return make_node("special", condition=cond)
            if kind == "NUMBER" and self.last_subject:
                self.take()
                return make_node("course", code=f"{self.last_subject} {value}")
            if kind == "LPAREN":
                self.take()
                result = self.parse_or()
                if self.peek()[0] == "RPAREN":
                    self.take()
                return result
            if kind == "WORD":
                self.unknown_words.append(value)
                self.take()
                continue
            if kind in {"AND", "OR"}:
                self.take()
                continue
            if kind not in {"EOF", "RPAREN"}:
                self.take()
            break
        return None


def strip_recommendation_phrases(text: str) -> str:
    return RECOMMEND_RE.sub("", text)


def commas_to_semicolons(text: str) -> str:
    depth = 0
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            rest = text[i + 1:].lstrip().lower()
            if rest.startswith("and ") or rest.startswith("or "):
                continue
            chars[i] = ";"
    return "".join(chars)


def parse_grouped_expression(text: str):
    text = normalize_text(text)
    text = re.sub(r";\s*or,?", " or ", text, flags=re.IGNORECASE)
    text = re.sub(r"\.\s+", "; ", text)
    text = re.sub(r"\([^)]*(?:see\s+below|also\s+listed|same\s+as)[^)]*\)", "", text, flags=re.IGNORECASE)
    text = commas_to_semicolons(text)
    text = re.sub(r"\b(junior|senior|sophomore|graduate)/(junior|senior|sophomore|graduate)\s+standing\b", r"\1 standing or \2 standing", text, flags=re.IGNORECASE)
    text = expand_slash_alternatives(text)
    text = expand_same_subject_options(text)
    groups = [
        re.sub(r"^\s*(?:and|or)\s+", "", part.strip(" ."), flags=re.IGNORECASE)
        for part in text.split(";")
        if part.strip(" .")
    ]
    parsed_groups = []
    unknown_words = []
    for group in groups:
        is_recommend = bool(RECOMMEND_RE.search(group))
        clean_group = RECOMMEND_RE.sub("", group).strip()
        if not clean_group:
            continue
        parser = ExpressionParser(tokenize(clean_group))
        parsed = parser.parse()
        unknown_words.extend(parser.unknown_words)
        if parsed:
            if is_recommend:
                parsed = make_node("recommend", [parsed])
            parsed_groups.append(parsed)
    if not parsed_groups:
        return None, unknown_words
    if len(parsed_groups) == 1:
        return simplify(parsed_groups[0]), unknown_words
    return simplify(make_node("and", parsed_groups)), unknown_words


def enrich_equivalent_nodes(tree):
    if not isinstance(tree, dict):
        return tree
    items = tree.get("items", [])
    if isinstance(items, list) and tree.get("type") == "or":
        equivalent_index = None
        for i, item in enumerate(items):
            if isinstance(item, dict) and item.get("type") == "special" and item.get("condition") == "equivalent_course_or_background":
                equivalent_index = i
                break
        if equivalent_index is not None:
            courses = [
                prev.get("code", "")
                for prev in items[:equivalent_index]
                if isinstance(prev, dict) and prev.get("type") == "course"
            ]
            if courses:
                items[equivalent_index]["equivalent_to"] = " or ".join(courses)
    for item in items:
        enrich_equivalent_nodes(item)
    return tree


def normalize_grade_nodes(tree):
    GRADE = {"sophomore": 2, "junior": 3, "senior": 4, "graduate": 5}
    if not isinstance(tree, dict):
        return tree
    if tree.get("type") == "special":
        cond = tree.get("condition", "")
        for level, num in GRADE.items():
            if cond == f"{level}_standing":
                tree["condition"] = "standing"
                tree["grade"] = num
                break
            if cond == f"{level}_standing_or_above":
                tree["condition"] = "standing_above"
                tree["grade"] = num
                break
    else:
        for item in tree.get("items", []):
            normalize_grade_nodes(item)
    return tree


def collect_detail_notes(tree, kind="", notes=None):
    if notes is None:
        notes = []
    if not isinstance(tree, dict):
        return notes
    current_kind = {"pre": "prerequisite", "corequisite": "corequisite", "pre_or_co": "pre_or_corequisite", "anti": "antirequisite"}.get(tree.get("type", ""), kind)
    if tree.get("type") == "course" and tree.get("notation"):
        notes.append(f"{current_kind}: {tree['code']} notation: {tree['notation']}")
    if tree.get("type") == "special":
        cond = tree.get("condition", "")
        if cond == "equivalent_course_or_background" and tree.get("equivalent_to"):
            notes.append(f"{current_kind}: equivalent to {tree['equivalent_to']}")
        if cond == "placement" and tree.get("text"):
            notes.append(f"{current_kind}: {tree['text']}")
    for item in tree.get("items", []):
        collect_detail_notes(item, current_kind, notes)
    return notes


def has_notation(tree):
    if not isinstance(tree, dict):
        return False
    if tree.get("notation"):
        return True
    for item in tree.get("items", []):
        if has_notation(item):
            return True
    return False


def course(code, role="pre"):
    return {"type": "course", "code": code, "role": role}


def build_eval_tree(pre=None, co=None, pre_or_co=None, anti=None):
    parts = []
    parts.append(eval_wrap(pre, "pre") or {"type": "bool", "value": True})
    if pre_or_co:
        parts.append(eval_wrap(pre_or_co, "pre_or_co"))
    parts.append(eval_wrap(co, "corequisite") or {"type": "bool", "value": True})
    parts.append(eval_wrap(anti, "anti") or {"type": "bool", "value": True})
    return {"type": "and", "items": parts}


def eval_wrap(tree, role):
    if not tree:
        return None
    if isinstance(tree, dict) and tree.get("type") == "special" and tree.get("condition") == "bool":
        return tree
    return {"type": role, "items": [tree] if isinstance(tree, dict) else tree}


def normalize_eval_tree(tree):
    return simplify_bool_tree(normalize_eval_node(tree))


def normalize_eval_node(node):
    if not isinstance(node, dict):
        return node
    node_type = node.get("type")
    if node_type in {"and", "or"}:
        return simplify_bool_tree(make_node(
            node_type,
            [normalize_eval_node(item) for item in node.get("items", [])],
        ))
    if node_type == "pre":
        return apply_requisite_relation("pre", unwrap_single_item(node))
    if node_type in {"co", "corequisite"}:
        return apply_requisite_relation("co", unwrap_single_item(node))
    if node_type == "pre_or_co":
        return apply_requisite_relation("pre_or_co", unwrap_single_item(node))
    if node_type == "anti":
        return normalize_anti_relation(unwrap_single_item(node))
    if node_type == "recommend":
        return normalize_recommend_node(node)
    if node_type == "bool":
        return {"type": "bool", "value": bool(node.get("value"))}
    if node_type == "course":
        return clean_course_node(node)
    if node_type == "special":
        return dict(node)
    cloned = dict(node)
    if "items" in cloned:
        cloned["items"] = [normalize_eval_node(item) for item in cloned.get("items", [])]
    return cloned


def unwrap_single_item(node):
    items = node.get("items", []) if isinstance(node, dict) else []
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return make_node("and", items)


def apply_requisite_relation(relation, target):
    if not target:
        return {"type": "bool", "value": True}
    if not isinstance(target, dict):
        return target
    target_type = target.get("type")
    if target_type in {"and", "or"}:
        return simplify_bool_tree(make_node(
            target_type,
            [apply_requisite_relation(relation, item) for item in target.get("items", [])],
        ))
    if target_type == "recommend":
        recommended = normalize_recommend_node(target)
        if isinstance(recommended, dict) and recommended.get("type") in {"and", "or"}:
            return apply_requisite_relation(relation, recommended)
        if relation == "pre_or_co":
            return simplify_bool_tree(make_node("or", [
                make_relation_leaf("pre", recommended),
                make_relation_leaf("co", recommended),
            ]))
        return make_relation_leaf(relation, recommended)
    if target_type == "special":
        return dict(target)
    if target_type == "course":
        course_node = clean_course_node(target)
        if relation == "pre_or_co":
            return simplify_bool_tree(make_node("or", [
                make_relation_leaf("pre", course_node),
                make_relation_leaf("co", course_node),
            ]))
        return make_relation_leaf(relation, course_node)
    if target_type == "bool":
        return {"type": "bool", "value": bool(target.get("value"))}
    normalized = normalize_eval_node(target)
    if isinstance(normalized, dict) and normalized.get("type") in {"and", "or"}:
        return apply_requisite_relation(relation, normalized)
    if relation == "pre_or_co":
        return simplify_bool_tree(make_node("or", [
            make_relation_leaf("pre", normalized),
            make_relation_leaf("co", normalized),
        ]))
    return make_relation_leaf(relation, normalized)


def make_relation_leaf(relation, target):
    relation = "co" if relation == "corequisite" else relation
    return {"type": relation, "items": [target]}


def normalize_recommend_node(node):
    target = unwrap_single_item(node)
    if not target:
        return {"type": "bool", "value": True}
    if not isinstance(target, dict):
        return {"type": "recommend", "items": [target]}
    target_type = target.get("type")
    if target_type in {"and", "or"}:
        return simplify_bool_tree(make_node(
            target_type,
            [normalize_recommend_node({"type": "recommend", "items": [item]}) for item in target.get("items", [])],
        ))
    if target_type == "recommend":
        return normalize_recommend_node(target)
    if target_type == "course":
        return {"type": "recommend", "items": [clean_course_node(target)]}
    if target_type == "special":
        return {"type": "recommend", "items": [dict(target)]}
    if target_type == "bool":
        return {"type": "bool", "value": bool(target.get("value"))}
    return {"type": "recommend", "items": [normalize_eval_node(target)]}


def normalize_anti_relation(target):
    if not target:
        return {"type": "bool", "value": True}
    courses = collect_course_leaves(target)
    if not courses:
        return {"type": "special", "condition": "unknown_antirequisite", "text": compact_json(target)}
    items = []
    for course_node in courses:
        items.append(make_not_node(make_relation_leaf("pre", course_node)))
        items.append(make_not_node(make_relation_leaf("co", course_node)))
    return simplify_bool_tree(make_node("and", items))


def collect_course_leaves(tree):
    courses = []
    seen = set()

    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "course":
            clean = clean_course_node(node)
            code = clean.get("code", "")
            if code and code not in seen:
                seen.add(code)
                courses.append(clean)
            return
        for item in node.get("items", []):
            visit(item)

    visit(tree)
    return courses


def clean_course_node(node):
    return {key: value for key, value in node.items() if key != "role"}


def make_not_node(node):
    return {"type": "not", "item": node}


def simplify_bool_tree(tree):
    if not isinstance(tree, dict):
        return tree
    tree_type = tree.get("type")
    if tree_type in {"and", "or"}:
        simplified = [simplify_bool_tree(item) for item in tree.get("items", [])]
        items = []
        for item in simplified:
            if not item:
                continue
            if isinstance(item, dict) and item.get("type") == tree_type:
                items.extend(item.get("items", []))
            else:
                items.append(item)
        if tree_type == "and":
            if any(is_bool_node(item, False) for item in items):
                return {"type": "bool", "value": False}
            items = [item for item in items if not is_bool_node(item, True)]
            if not items:
                return {"type": "bool", "value": True}
        else:
            if any(is_bool_node(item, True) for item in items):
                return {"type": "bool", "value": True}
            items = [item for item in items if not is_bool_node(item, False)]
            if not items:
                return {"type": "bool", "value": False}
        if len(items) == 1:
            return items[0]
        return make_node(tree_type, items)
    if tree_type == "not":
        item = simplify_bool_tree(tree.get("item"))
        if is_bool_node(item, True):
            return {"type": "bool", "value": False}
        if is_bool_node(item, False):
            return {"type": "bool", "value": True}
        if isinstance(item, dict) and item.get("type") == "not":
            return simplify_bool_tree(item.get("item"))
        return {"type": "not", "item": item}
    if "items" in tree:
        cloned = dict(tree)
        cloned["items"] = [simplify_bool_tree(item) for item in tree.get("items", [])]
        return cloned
    return dict(tree)


def is_bool_node(node, value):
    return isinstance(node, dict) and node.get("type") == "bool" and bool(node.get("value")) is value


def compact_json(value) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def collect_hc_specials(tree):
    conds = []
    if not isinstance(tree, dict):
        return conds
    if tree.get("type") == "special":
        c = tree.get("condition", "")
        if c == "consent_of_instructor":
            return conds
        if c == "unknown":
            conds.append("unknown")
        else:
            conds.append(c)
    for item in tree.get("items", []):
        conds.extend(collect_hc_specials(item))
    return conds


# Hardcoded rules for known problematic texts
_cs_stats = {
    "type": "or", "items": [
        course("COMPSCI 101", "pre"),
        course("COMPSCI 201", "pre"),
        course("STATS 102", "pre"),
    ]
}
_stats_pre = {
    "type": "or", "items": [
        course("STATS 302", "pre"),
        course("COMPSCI 309", "pre"),
        {"type": "and", "items": [course("MATH 405", "pre"), course("COMPSCI 201", "pre")]},
    ]
}
_stats_co = {
    "type": "or", "items": [
        course("STATS 302", "co"),
        course("COMPSCI 309", "co"),
        {"type": "and", "items": [course("MATH 405", "co"), course("COMPSCI 201", "co")]},
    ]
}
_polsci_104_previous_201 = {
    "type": "course",
    "code": "POLSCI 104",
    "role": "pre",
    "notation": "previously POLSCI 201",
}

HARDCODED = {
    "Prerequisite: EAP track students must complete EAP 102B before enrolling.": {
        "eval_tree": build_eval_tree(
            pre={"type": "or", "items": [
                {"type": "special", "condition": "not_eap_track"},
                course("EAP 102B"),
            ]}
        )
    },
    "Prerequisite: COMPSCI 101 or COMPSCI 201 or STATS 102 and MATH 201, MATH 202; Anti-requisite: MATH 304": {
        "eval_tree": build_eval_tree(
            pre={"type": "and", "items": [_cs_stats, course("MATH 201"), course("MATH 202")]},
            anti=course("MATH 304", "anti"),
        )
    },
    "Prerequisite: COMPSCI 201; and COMPSCI 205 as the prerequisite or co-requisite": {
        "eval_tree": build_eval_tree(
            pre=course("COMPSCI 201"),
            pre_or_co=course("COMPSCI 205", "pre_or_co"),
        )
    },
    "Prerequisite: POLECON 201 or POLSCI 104 (previously POLSCI 201) and Senior standing": {
        "eval_tree": build_eval_tree(
            pre={"type": "and", "items": [
                {"type": "or", "items": [course("POLECON 201"), _polsci_104_previous_201]},
                {"type": "special", "condition": "standing", "grade": 4},
            ]}
        )
    },
    "Prerequisite: STATS 302 or COMPSCI 309 or (MATH 405 and COMPSCI 201) or Co-requisite upon Consent of Instructor": {
        "eval_tree": {"type": "or", "items": [
            {"type": "pre", "items": [_stats_pre]},
            {"type": "and", "items": [
                {"type": "special", "condition": "consent_of_instructor"},
                {"type": "corequisite", "items": [_stats_co]},
            ]},
        ]}
    },
    "Pre-requisite: STATS 302 or COMPSCI 309 or (MATH 405 and COMPSCI 201) or Co-requisite upon Consent of Instructor": {
        "eval_tree": {"type": "or", "items": [
            {"type": "pre", "items": [_stats_pre]},
            {"type": "and", "items": [
                {"type": "special", "condition": "consent_of_instructor"},
                {"type": "corequisite", "items": [_stats_co]},
            ]},
        ]}
    },
    "Prerequisite: INTGSCI 102; or CHEM 110 or CHEM 120, and PHYS 121": {
        "eval_tree": build_eval_tree(
            pre={"type": "or", "items": [
                course("INTGSCI 102"),
                {"type": "and", "items": [
                    {"type": "or", "items": [course("CHEM 110"), course("CHEM 120")]},
                    course("PHYS 121"),
                ]},
            ]}
        )
    },
    "Prerequisite: MATH 101 or 105 and MATH 205 or MATH 206 or equivalent probability course. Some coding experience is encouraged, but not strictly required.": {
        "eval_tree": build_eval_tree(
            pre={"type": "and", "items": [
                {"type": "or", "items": [course("MATH 101"), course("MATH 105")]},
                {"type": "or", "items": [
                    course("MATH 205"),
                    course("MATH 206"),
                    {"type": "special", "condition": "equivalent_course_or_background", "equivalent_to": "probability course"},
                ]},
                {"type": "special", "condition": "unknown", "text": "Some coding experience is encouraged, but not strictly required."},
            ]}
        )
    },
}


def extract_requirements_from_detail_json(raw_json: str) -> str:
    try:
        data = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    section = data.get("section_info", {}) or {}
    if not isinstance(section, dict):
        return ""
    enrollment = section.get("enrollment_information", {}) or {}
    if not isinstance(enrollment, dict):
        return ""
    return str(enrollment.get("enroll_requirements", "") or "").strip()


def parse_requisite_text(text: str):
    text = normalize_text(text)
    if text in HARDCODED:
        hc = HARDCODED[text]
        tree = hc["eval_tree"]
        return {
            "raw": text,
            "eval_tree": tree,
            "normalized_eval_tree": normalize_eval_tree(tree),
            "special_conditions": collect_hc_specials(tree),
            "notes": collect_detail_notes(tree) + ["hardcoded relation: " + text],
            "status": "partial",
        }

    result = {
        "raw": text,
        "special_conditions": extract_specials(text),
        "notes": [],
        "status": "failed",
    }

    trees = {"prerequisite": None, "corequisite": None, "pre_or_corequisite": None, "antirequisite": None}
    any_tree = False

    for kind, body in split_sections(text):
        tree, unknown_words = parse_grouped_expression(body)
        unknown_words = [word for word in unknown_words if word.lower() not in IGNORED_WORDS]
        if unknown_words:
            result["notes"].append(f"{kind}: \u672a\u89e3\u6790\u8bcd\u8bed {' '.join(unknown_words)}")
            unknown_node = make_node("special", condition="unknown", text=" ".join(unknown_words))
            tree = make_node("and", [tree, unknown_node]) if tree else unknown_node
            result["special_conditions"].append("unknown")
        if tree:
            any_tree = True
            if kind == "unlabeled":
                kind = "prerequisite"
            existing = trees.get(kind)
            trees[kind] = simplify(make_node("and", [existing, tree])) if existing else tree

    for kind in ["prerequisite", "corequisite", "pre_or_corequisite", "antirequisite"]:
        t = trees[kind]
        if t:
            t = enrich_equivalent_nodes(t)
            t = normalize_grade_nodes(t)
            trees[kind] = t
            if has_notation(t):
                result["special_conditions"].append("notation")
                result["status"] = "partial"
            for note in collect_detail_notes(t, kind):
                result["notes"].append(note)

    if re.search(r"\bor\b", text, re.IGNORECASE) and re.search(r"\band\b", text, re.IGNORECASE):
        segments = [s for s in re.split(r"[;(]", text) if s.strip()]
        for segment in segments:
            if re.search(r"\bor\b", segment, re.IGNORECASE) and re.search(r"\band\b", segment, re.IGNORECASE):
                result["notes"].append("potential AND/OR precedence ambiguity: '" + segment.strip()[:50] + "'")
                result["special_conditions"].append("precedence")
                result["status"] = "partial"
                break

    if any_tree and not result["notes"] and not result["special_conditions"]:
        result["status"] = "parsed"
    elif any_tree or result["special_conditions"]:
        result["status"] = "partial"

    eval_tree = build_eval_tree(
        pre=trees["prerequisite"],
        co=trees["corequisite"],
        pre_or_co=trees["pre_or_corequisite"],
        anti=trees["antirequisite"],
    )
    result["eval_tree"] = eval_tree
    result["normalized_eval_tree"] = normalize_eval_tree(eval_tree)
    return result


def classify_requisite(parsed: dict) -> list:
    """Return tags like ['has_prereq', 'has_grade', 'has_consent', 'has_unknown']"""
    if not parsed:
        return []
    tags = []
    tree = parsed.get("eval_tree", {})

    def has_type(t, ttype):
        if not isinstance(t, dict):
            return False
        if t.get("type") == ttype:
            return True
        for item in t.get("items", []):
            if has_type(item, ttype):
                return True
        return False

    def has_condition(t, cond):
        if not isinstance(t, dict):
            return False
        if t.get("type") == "special":
            c = t.get("condition", "")
            if c == cond:
                return True
            # standing/standing_above/consent → grade check
            if cond == "grade" and (c == "standing" or c == "standing_above" or c.startswith("standing")):
                return True
        if t.get("type") == "not":
            return has_condition(t.get("item"), cond)
        for item in t.get("items", []):
            if has_condition(item, cond):
                return True
        return False

    if has_type(tree, "pre"):
        tags.append("has_prereq")
    if has_type(tree, "co") or has_type(tree, "corequisite"):
        tags.append("has_coreq")
    if has_type(tree, "anti"):
        tags.append("has_anti")
    if has_condition(tree, "grade"):
        tags.append("has_grade")
    if has_condition(tree, "consent_of_instructor"):
        tags.append("has_consent")
    if has_condition(tree, "unknown") or has_condition(tree, "precedence"):
        tags.append("has_unknown")
    # Also check special_conditions for legacy formatting issues
    conds = [s.lower() for s in parsed.get("special_conditions", [])]
    if "unknown" in conds or "precedence" in conds:
        tags.append("has_unknown")

    return list(dict.fromkeys(tags))
