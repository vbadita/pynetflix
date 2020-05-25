from selenium import webdriver
import os
import csv
import time
from argparse import ArgumentParser

parser = ArgumentParser(description='Get Netflix account viewing history')
parser.add_argument('account', help='Netflix account to be used with script')

args = parser.parse_args()

if "NETFLIX_EMAIL" and "NETFLIX_PASSWORD" not in os.environ:
    print("Please set environment variables for NETFLIX_EMAIL AND NETFLIX_PASSWORD")
    sys.exit(0)

username = os.environ['NETFLIX_EMAIL']
password = os.environ['NETFLIX_PASSWORD']
account = args.account


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


def get_history(account):
    profile_to_click = driver.find_element_by_xpath(f"//span[@class='profile-name' and contains(text(),'{account}')]")
    profile_to_click.click()
    driver.get(netflix_activity_page)
    activity_button = driver.find_element_by_class_name("viewing-activity-footer-download")
    activity_button.click()


if __name__ == '__main__':
    netflix_connected = connect(username,password)
    get_history(account)
    driver.quit()
