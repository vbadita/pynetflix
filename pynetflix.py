from selenium import webdriver
import os

username = os.environ['NETFLIX_EMAIL']
password = os.environ['NETFLIX_PASSWORD']

def connect(username,password):
    driver = webdriver.Firefox()
    driver.implicitly_wait(3)
    driver.get("https://netflix.com")
    menu =  driver.find_element_by_class_name('authLinks')
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

def get_history(connected, profile):
    #to be done





if __name__ == '__main__':
    netflix_connected = connect(username,password)