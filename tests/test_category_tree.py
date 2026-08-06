from uuid import uuid4

from src.catalog.schemas import CategorySnapshot
from src.commerce.category_tree import (
    build_category_forest,
    category_and_descendant_ids,
    category_subtree,
)


def _category(
    *,
    name: str,
    slug: str,
    sort_order: int,
    parent_id=None,
) -> CategorySnapshot:
    return CategorySnapshot(
        id=uuid4(),
        parent_id=parent_id,
        slug=slug,
        name=name,
        description=None,
        sort_order=sort_order,
    )


def test_build_category_forest_nests_children_under_roots() -> None:
    men = _category(name="Men", slug="men", sort_order=10)
    women = _category(name="Women", slug="women", sort_order=20)
    running = _category(name="Men Running", slug="men-running", sort_order=11, parent_id=men.id)
    casual = _category(name="Men Casual", slug="men-casual", sort_order=12, parent_id=men.id)

    forest = build_category_forest((casual, running, women, men))
    assert [node.slug for node in forest] == ["men", "women"]
    assert [child.slug for child in forest[0].children] == ["men-running", "men-casual"]
    assert forest[1].children == ()


def test_category_subtree_returns_nested_descendants() -> None:
    root = _category(name="Men", slug="men", sort_order=10)
    child = _category(name="Boots", slug="boots", sort_order=1, parent_id=root.id)
    leaf = _category(name="Hikers", slug="hikers", sort_order=1, parent_id=child.id)

    node = category_subtree((root, child, leaf), "men")
    assert node is not None
    assert node.slug == "men"
    assert node.children[0].slug == "boots"
    assert node.children[0].children[0].slug == "hikers"

    by_id = category_subtree((root, child, leaf), str(child.id))
    assert by_id is not None
    assert by_id.slug == "boots"
    assert [item.slug for item in by_id.children] == ["hikers"]


def test_missing_parent_becomes_root() -> None:
    orphan = _category(name="Orphan", slug="orphan", sort_order=1, parent_id=uuid4())
    forest = build_category_forest((orphan,))
    assert len(forest) == 1
    assert forest[0].slug == "orphan"
    assert forest[0].children == ()


def test_category_filter_includes_descendant_ids() -> None:
    men = _category(name="Men", slug="men", sort_order=10)
    running = _category(
        name="Men Running", slug="men-running", sort_order=11, parent_id=men.id
    )
    boots = _category(name="Men Boots", slug="men-boots", sort_order=13, parent_id=men.id)
    selected = category_and_descendant_ids((men, running, boots), "men")
    assert selected == {men.id, running.id, boots.id}
    assert category_and_descendant_ids((men, running, boots), "missing") is None
