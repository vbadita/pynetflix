from selenium import webdriver

if __name__ == '__main__':
    driver = webdriver.Firefox()
    driver.get("https://netflix.com")
    assert "Neftlix" in driver.title
    print(driver.title)
    driver.close()