import os

import markdown
import yaml
from flask import abort, render_template

from . import bp

POSTS_DIR = os.path.join(os.path.dirname(__file__), "posts")


def _load_post(slug):
    """Load a single post by slug from its .md file."""
    filepath = os.path.join(POSTS_DIR, f"{slug}.md")
    if not os.path.isfile(filepath):
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    # Split YAML frontmatter from body
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1])
            body = parts[2].strip()
        else:
            return None
    else:
        return None

    meta.setdefault("slug", slug)

    md = markdown.Markdown(extensions=["extra", "codehilite", "toc"])
    content = md.convert(body)

    return {"meta": meta, "content": content}


def _load_all_posts():
    """Load all posts, sorted by date descending."""
    posts = []
    if not os.path.isdir(POSTS_DIR):
        return posts

    for filename in os.listdir(POSTS_DIR):
        if not filename.endswith(".md"):
            continue
        slug = filename[:-3]
        post = _load_post(slug)
        if post:
            posts.append(post)

    posts.sort(key=lambda p: str(p["meta"].get("date", "")), reverse=True)
    return posts


@bp.route("/blog/")
def blog_index():
    posts = _load_all_posts()
    return render_template("blog/index.html", posts=posts)


@bp.route("/blog/<slug>")
def blog_post(slug):
    post = _load_post(slug)
    if not post:
        abort(404)
    return render_template(
        "blog/post.html",
        post=post["meta"],
        content=post["content"],
    )
