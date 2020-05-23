from selenium import webdriver
import os
import csv
import time

username = os.environ['NETFLIX_EMAIL']
password = os.environ['NETFLIX_PASSWORD']
account = os.environ['NETFLIX_ACCOUNT']


profile = webdriver.FirefoxProfile()
#profile.set_preference('browser.download.folderList', 2) # custom location
#profile.set_preference('browser.download.manager.showWhenStarting', False)
#profile.set_preference('browser.download.dir', '/tmp')
profile.set_preference('browser.helperApps.neverAsk.saveToDisk', 'text/csv')

driver = webdriver.Firefox(profile)
netflix_page = "https://netflix.com"
netflix_activity_page = "https://www.netflix.com/viewingactivity"


def connect(username,password):
    driver.implicitly_wait(5)
    driver.get(netflix_page)
    driver.implicitly_wait(2)
    # Closes cookie banner
    cookie_bar = driver.find_element_by_class_name('close-button')
    cookie_bar.click()
    driver.implicitly_wait(2)
    menu = driver.find_element_by_class_name('authLinks')
    actions = webdriver.ActionChains(driver)
    actions.move_to_element(menu)
    actions.click(menu)
    actions.perform()
    input_username = driver.find_element_by_id("id_userLoginId")
    input_password = driver.find_element_by_id("id_password")
    login_button = driver.find_element_by_class_name("login-button")
    input_username.send_keys(username)
    input_password.send_keys(password)
    login_button.click()
    return driver


def get_history(profile):
    profile_to_click = driver.find_element_by_xpath(f"//span[@class='profile-name' and contains(text(),'{profile}')]")
    profile_to_click.click()
    driver.get(netflix_activity_page)
    activity_button = driver.find_element_by_class_name("viewing-activity-footer-download")
    activity_button.click()
    # Below code not needed - downloads file twice in case needed later
    # download_page = driver.find_element_by_class_name("modal-action-button")
    # download_page.click()


if __name__ == '__main__':
    netflix_connected = connect(username,password)
    get_history(account)
