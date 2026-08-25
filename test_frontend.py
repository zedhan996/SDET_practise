import time

from playwright.sync_api import Page, expect


def open_login_page(page: Page, base_url: str) -> None:
    """打开页面并等待首次登录态检查完成，避免与表单操作发生竞态。"""
    with page.expect_response(lambda response: response.url.endswith("/web/me")):
        page.goto(f"{base_url}/app/", wait_until="domcontentloaded")
    expect(page.locator("#login-view")).to_be_visible()


def login_to_workspace(page: Page, base_url: str) -> None:
    """完成登录，供需要登录状态的 UI 用例复用。"""
    open_login_page(page, base_url)
    page.locator("#login-username").fill("admin")
    page.locator("#login-password").fill("Admin@123")
    page.get_by_role("button", name="进入工作台").click()
    expect(page.locator("#workspace-view")).to_be_visible()


def test_frontend_login_shows_catalog(page: Page, base_url: str) -> None:
    """验证用户可以通过真实浏览器登录并进入商品目录工作台。"""
    login_to_workspace(page, base_url)

    # 先确认打开的是项目业务页面，而不是错误页或 API 文档页。
    expect(page).to_have_title("商品目录工作台")

    expect(page.get_by_role("heading", name="商品目录", exact=True)).to_be_visible()


def test_frontend_invalid_login_shows_error(page: Page, base_url: str) -> None:
    """验证错误密码不会进入工作台，并显示后端返回的错误信息。"""
    open_login_page(page, base_url)

    page.locator("#login-username").fill("admin")
    page.locator("#login-password").fill("WrongPwd123")
    page.get_by_role("button", name="进入工作台").click()

    expect(page.locator("#login-message")).to_have_text("Incorrect password")
    expect(page.locator("#workspace-view")).to_be_hidden()


def test_frontend_search_filters_catalog(page: Page, base_url: str) -> None:
    """验证输入商品关键词后，页面只展示匹配的目录结果。"""
    login_to_workspace(page, base_url)

    page.locator("#search-keyword").fill("iPhone 13")
    page.get_by_role("button", name="搜索").click()

    results = page.get_by_test_id("catalog-row")
    expect(results).to_have_count(1)
    expect(results.first.locator(".item-name")).to_have_text("iPhone 13")


def test_frontend_session_survives_refresh(page: Page, base_url: str) -> None:
    """验证浏览器刷新后，HttpOnly Cookie 可以恢复登录状态。"""
    login_to_workspace(page, base_url)

    page.reload(wait_until="domcontentloaded")

    expect(page.locator("#workspace-view")).to_be_visible()
    expect(page.locator("#user-badge")).to_have_text("admin · 已登录")


def test_frontend_logout_clears_session(page: Page, base_url: str) -> None:
    """验证退出登录后，刷新页面也不能恢复工作台。"""
    login_to_workspace(page, base_url)

    page.get_by_role("button", name="退出登录").click()
    expect(page.locator("#login-view")).to_be_visible()

    page.reload(wait_until="domcontentloaded")

    expect(page.locator("#login-view")).to_be_visible()
    expect(page.locator("#workspace-view")).to_be_hidden()


def test_frontend_can_edit_and_restore_item(page: Page, base_url: str) -> None:
    """验证编辑商品成功，并在 finally 中恢复测试前的名称和价格。"""
    login_to_workspace(page, base_url)

    item_id = 101
    row = page.locator(f'[data-testid="catalog-row"][data-item-id="{item_id}"]')
    expect(row).to_be_visible()
    row.get_by_test_id("edit-item").click()

    original_name = page.locator("#item-name").input_value()
    original_price = page.locator("#item-price").input_value()
    updated_name = f"{original_name} UI"
    updated_price = f"{float(original_price) + 1:.2f}"
    updated = False

    try:
        page.locator("#item-name").fill(updated_name)
        page.locator("#item-price").fill(updated_price)
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/items/{item_id}")
                and response.request.method == "PUT"
            )
        ) as response_info:
            page.get_by_role("button", name="保存商品").click()

        assert response_info.value.status == 200
        updated = True
        expect(row.locator(".item-name")).to_have_text(updated_name)
        expect(row.locator(".item-value strong").first).to_have_text(
            f"¥ {float(updated_price):.2f}"
        )
    finally:
        if updated:
            row.get_by_test_id("edit-item").click()
            page.locator("#item-name").fill(original_name)
            page.locator("#item-price").fill(original_price)
            with page.expect_response(
                lambda response: (
                    response.url.endswith(f"/items/{item_id}")
                    and response.request.method == "PUT"
                )
            ) as restore_response:
                page.get_by_role("button", name="保存商品").click()

            assert restore_response.value.status == 200
            expect(row.locator(".item-name")).to_have_text(original_name)


def test_frontend_can_create_and_remove_item(page: Page, base_url: str) -> None:
    """验证页面可以新增商品，并在测试结束时清理临时数据。"""
    login_to_workspace(page, base_url)

    item_id = int(time.time())
    item_name = f"Playwright 临时商品 {item_id}"

    try:
        page.get_by_role("button", name="新增商品").click()
        expect(page.locator("#item-dialog")).to_be_visible()

        page.locator("#item-id").fill(str(item_id))
        page.locator("#item-name").fill(item_name)
        page.locator("#item-price").fill("19.99")
        page.locator("#item-category").fill("test")
        page.get_by_role("button", name="保存商品").click()

        created_row = page.get_by_test_id("catalog-row").filter(has_text=item_name)
        expect(created_row).to_be_visible()
        expect(created_row.locator(".item-name")).to_have_text(item_name)
    finally:
        # 无论断言是否成功，都尝试刷新并删除本次测试创建的临时商品。
        if page.locator("#item-dialog[open]").count():
            page.get_by_role("button", name="取消").click()

        with page.expect_response(
            lambda response: (
                response.url.startswith(f"{base_url}/items/search")
                and response.request.method == "GET"
            )
        ):
            page.get_by_role("button", name="刷新目录").click()

        created_row = page.get_by_test_id("catalog-row").filter(has_text=item_name)
        if created_row.count():
            page.once("dialog", lambda dialog: dialog.accept())
            created_row.get_by_role("button", name="删除").click()
            expect(created_row).to_have_count(0)
