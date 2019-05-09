from selenium import webdriver

driver = webdriver.Firefox()
driver.get("https://netflix.com")
print(driver.title)
driver.close