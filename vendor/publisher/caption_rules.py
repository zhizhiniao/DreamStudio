"""Pure caption rules shared by studio preflight and the upload worker."""
KUAISHOU_LIMIT = 500


def kuaishou_count(text):
    """Match the live web form's JavaScript UTF-16 length (emoji can count twice)."""
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


def kuaishou_caption(title, description, tags):
    return '\n'.join(p for p in (title.strip(), description.strip(), ' '.join('#'+t for t in tags)) if p)


def kuaishou_issues(title, description, tags):
    issues=[]
    if len(tags)>3:issues.append('快手最多 3 个话题')
    count=kuaishou_count(kuaishou_caption(title,description,tags))
    if count>KUAISHOU_LIMIT:
        issues.append(f'快手标题、简介和话题合计 {count} 字，最多 {KUAISHOU_LIMIT} 字（含换行、空格和 #）')
    return issues
