from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from sailwind_mod_sync.models import CatalogEntry, PinnedMod


def _visible_guids(view):
    return [view.table.item(row, 2).text() for row in range(view.table.rowCount())
            if not view.table.isRowHidden(row)]


def _filter(view):
    return next(edit for edit in view.findChildren(QLineEdit) if edit.placeholderText() == "Filter mods…")


def _populate(manager, window):
    pack = manager.packs.get(window.current_pack_id())
    for guid in ("example.apple", "example.zebra"):
        manager.packs.upsert_mod(pack.id, PinnedMod(guid=guid, version="1.0.0"))
    manager.aliases["example.apple"] = "Orchard Cargo"
    window._reload_views()
    return pack


def test_filter_matches_names_and_guids_after_sort_and_refresh(window, manager):
    _, window = window
    _populate(manager, window)
    view = window.pack_view
    search = _filter(view)
    search.setText(" ORCHARD ")
    assert _visible_guids(view) == ["example.apple"]
    view.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    assert _visible_guids(view) == ["example.apple"]
    window._reload_views()
    assert search.text() == " ORCHARD "
    assert _visible_guids(view) == ["example.apple"]
    search.setText("EXAMPLE.ZEBRA")
    assert _visible_guids(view) == ["example.zebra"]
    search.clear()
    assert set(_visible_guids(view)) == {"example.apple", "example.zebra"}
    search.setText("missing query")
    assert not _visible_guids(view)
    other = manager.packs.create("Other")
    manager.packs.upsert_mod(other.id, PinnedMod(guid="other.mod", version="1.0.0"))
    window._reload_packs(select_id=other.id)
    assert search.text() == ""
    assert _visible_guids(view) == ["other.mod"]


def test_filter_keeps_whole_pack_checkbox_scope(window, manager, pump_until):
    app, window = window
    pack = _populate(manager, window)
    search = _filter(window.pack_view)
    search.setText("Orchard")
    window.pack_view.check_all.click()
    assert pump_until(app, lambda: not window._busy)
    assert all(mod.enabled for mod in manager.packs.get(pack.id).mods)
    assert _visible_guids(window.pack_view) == ["example.apple"]
    window.pack_view.uncheck_all.click()
    assert pump_until(app, lambda: not window._busy)
    assert not any(mod.enabled for mod in manager.packs.get(pack.id).mods)
    assert _visible_guids(window.pack_view) == ["example.apple"]


def test_filter_keeps_native_update_all_scope(window, manager):
    _, window = window
    _populate(manager, window)
    manager.catalog = [CatalogEntry(
        name=guid, repo=f"https://github.com/example/{guid.rsplit('.', 1)[-1]}",
        guids=[guid], primary_guid=guid, latest_raw="2.0.0", latest_version="2.0.0", available=True,
    ) for guid in ("example.apple", "example.zebra")]
    window._reload_views()
    view = window.pack_view
    _filter(view).setText("apple")
    updates = []
    view.update_all_requested.disconnect(window._update_all_mods)
    view.update_all_requested.connect(updates.append)
    view.update_all.click()
    assert updates == [["example.apple", "example.zebra"]]
