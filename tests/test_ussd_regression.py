"""Today's USSD behaviour (personal phone book), pinned down before the code is reorganised."""
from conftest import auth, login_ussd

ME = "+254712345678"
OTHER = "+254722000111"


def test_welcome_and_invalid_number(dial):
    s = dial(ME)
    assert s.last == "CON Welcome to Remote Phone-Book\nEnter your phone number:"
    assert s.send("12345").startswith("END Invalid phone number.")


def test_unregistered_number(dial):
    s = dial(ME)
    assert "is not registered" in s.send("0712345678")


def test_login_shows_main_menu(register, dial):
    register()
    s = dial(ME)
    assert s.send("0712345678") == "CON Enter your PIN:"
    menu = s.send("1234")
    assert menu.startswith("CON Remote Phone-Book\n1. My contacts\n2. Emergency numbers")
    assert "6. SMS me my contacts" in menu and "000. Log out" in menu


def test_wrong_pin_then_right(register, dial):
    register()
    s = dial(ME)
    s.send("0712345678")
    assert "Wrong PIN. You have 1 more try" in s.send("9999")
    assert s.send("1234").startswith("CON Remote Phone-Book")


def test_two_wrong_pins_end_with_reset_help(register, dial):
    register()
    s = dial(ME)
    s.send("0712345678")
    s.send("9999")
    body = s.send("8888")
    assert body.startswith("END Wrong PIN again.") and "Change USSD PIN" in body


def test_lockout_after_five_wrong_pins(register, dial):
    register()
    for _ in range(2):
        s = dial(ME)
        s.send("0712345678")
        s.send("1111")
        s.send("2222")
    s = dial(ME)
    s.send("0712345678")
    body = s.send("3333")
    assert body.startswith("END Too many wrong PINs")
    s = dial(ME)
    s.send("0712345678")
    assert s.send("1234").startswith("END Too many wrong PINs")  # right PIN is refused while locked


def test_add_list_search_delete(register, dial):
    register()
    s = login_ussd(dial, ME)
    assert "Enter contact name" in s.send("3")
    assert "Enter phone number for Juma" in s.send("Juma")
    assert "Saved Juma - +254733111222" in s.send("0733111222")
    assert "Invalid choice" in s.send("0700000000")  # nothing typed after a result runs again

    s = login_ussd(dial, ME)
    assert "Juma" in s.send("1")
    s = login_ussd(dial, ME)
    assert "Juma" in s.send("jum")  # typing a name on the main menu searches

    s = login_ussd(dial, ME)
    assert "Select contact to delete" in s.send("5")
    assert "Delete Juma" in s.send("1")
    assert "Juma deleted." in s.send("1")
    s = login_ussd(dial, ME)
    assert "no contacts yet" in s.send("1")


def test_list_pagination(register, dial, client):
    token = register()
    for i in range(6):
        client.post("/api/contacts", json={"name": f"Person {i}", "phone": f"07330000{i:02d}"}, headers=auth(token))
    s = login_ussd(dial, ME)
    page1 = s.send("1")
    assert "1." in page1 and "4." in page1 and "98. More" in page1 and "5." not in page1
    page2 = s.send("98")
    assert "5." in page2 and "6." in page2 and "98. More" not in page2
    assert "Person 5" in s.send("6")  # numbering continues across pages


def test_navigation_back_home_logout(register, dial):
    register()
    s = login_ussd(dial, ME)
    s.send("3")
    assert s.send("0").startswith("CON Remote Phone-Book")  # back
    s.send("2")
    assert s.send("00").startswith("CON Remote Phone-Book")  # home
    assert s.send("000").startswith("END You are logged out")


def test_emergency_numbers(register, dial):
    register()
    s = login_ussd(dial, ME)
    assert "999" in s.send("2")
    assert "1. Call now" in s.send("1")
    assert s.send("1") == "END Dial 999 now from your phone. It is free."
    s = login_ussd(dial, ME)
    s.send("2")
    s.send("1")
    assert "Saved" in s.send("2")
    s = login_ussd(dial, ME)
    s.send("2")
    s.send("1")
    assert "already in your contacts" in s.send("2")


def test_sms_me_my_contacts(register, dial, client, fake_sms):
    token = register()
    client.post("/api/contacts", json={"name": "Juma", "phone": "0733111222"}, headers=auth(token))
    s = login_ussd(dial, OTHER)  # from a borrowed phone: the SMS goes to the phone in use
    assert "You will receive your 1 contacts by SMS" in s.send("6")
    msgs = fake_sms.to(OTHER)
    assert any("Juma +254733111222" in m for m in msgs)


def test_contact_menu_sms_and_copy(register, dial, client, fake_sms):
    token = register()
    client.post("/api/contacts", json={"name": "Juma", "phone": "0733111222"}, headers=auth(token))
    s = login_ussd(dial, ME)
    s.send("1")
    assert "1. Call\n2. Send SMS\n3. Copy number" in s.send("1")
    assert "Calling isn't available" in s.send("1")  # no voice number configured
    assert "SMS Juma" in s.send("2")
    assert "Message sent to Juma." in s.send("1")
    assert any(m.startswith("Please call me.\n- Asha (0712345678)") for m in fake_sms.to("+254733111222"))

    s = login_ussd(dial, ME)
    s.send("1")
    s.send("1")
    s.send("2")
    s.send("2")
    assert "Message sent" in s.send("Running late")
    assert any(m.startswith("Running late") for m in fake_sms.to("+254733111222", count=2))

    s = login_ussd(dial, ME)
    s.send("1")
    s.send("1")
    assert "Also sent to you by SMS" in s.send("3")


def test_resume_on_own_phone(register, dial):
    register()
    s = login_ussd(dial, ME)
    s.send("3")  # leave the session on "Enter contact name"
    again = dial(ME)
    assert again.last.startswith("CON Welcome back")
    assert "1. Continue where you left off" in again.last
    assert "Enter PIN" in again.send("1")
    assert "Enter contact name" in again.send("1234")


def test_resume_on_borrowed_phone_asks_pin(register, dial):
    register()
    s = login_ussd(dial, OTHER)
    s.send("3")
    again = dial(OTHER)
    assert "Enter PIN for" in again.last
    assert "Enter contact name" in again.send("1234")


def test_logout_from_app_ends_ussd(register, dial, client):
    token = register()
    s = login_ussd(dial, ME)
    resp = client.post("/api/ussd/logout", json={}, headers=auth(token))
    assert resp.status_code == 200
    assert s.send("1").startswith("END You were logged out from the Remote Phonebook app")
