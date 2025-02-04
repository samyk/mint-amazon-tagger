import logging
import os
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait

from mintamazontagger.args import has_order_history_csv_files
from mintamazontagger.my_progress import no_progress_factory
from mintamazontagger.webdriver import get_element_by_id, get_element_by_xpath

logger = logging.getLogger(__name__)

ORDER_HISTORY_URL_VIA_SWITCH_ACCOUNT_LOGIN = (
    'https://www.amazon.com/gp/navigation/redirector.html/ref=sign-in-redirect'
    '?ie=UTF8&associationHandle=usflex&currentPageURL='
    'https%3A%2F%2Fwww.amazon.com%2Fgp%2Fyourstore%2Fhome%3Fie%3DUTF8%26'
    'ref_%3Dnav_youraccount_switchacct&pageType=&switchAccount=picker&'
    'yshURL=https%3A%2F%2Fwww.amazon.com%2Fgp%2Fb2b%2Freports')
ORDER_HISTORY_REPORT_URL = 'https://www.amazon.com/gp/b2b/reports'


def fetch_order_history(args, webdriver_factory,
                        progress_factory=no_progress_factory):
    if has_order_history_csv_files(args):
        return True

    name = (
        args.amazon_email.split('@')[0]
        if args.amazon_email else 'mint_tagger_unknown_user')

    start_date = args.order_history_start_date
    end_date = args.order_history_end_date
    report_shortnames = ['Items', 'Orders', 'Refunds']
    report_names = ['{} {} from {:%d %b %Y} to {:%d %b %Y}'.format(
                    name, t, start_date, end_date)
                    for t in report_shortnames]
    report_types = ['ITEMS', 'SHIPMENTS', 'REFUNDS']
    report_paths = [os.path.join(args.report_download_location, name + '.csv')
                    for name in report_names]

    os.makedirs(args.report_download_location, exist_ok=True)

    # Be lazy with getting the driver, as if no fetching is needed, then it's
    # all good.
    webdriver = None
    for report_shortname, report_type, report_name, report_path in zip(
            report_shortnames, report_types, report_names, report_paths):
        if os.path.exists(report_path):
            # Report has already been fetched! Woot
            continue

        # Report is not here. Go get it.
        if not webdriver:
            if ((not args.amazon_email or not args.amazon_password)
                    and not args.amazon_user_will_login):
                logger.error('No credentials provided for Amazon.com')
                return False
            login_progress = progress_factory(
                'Signing into Amazon.com to request order reports.', 0)
            webdriver = webdriver_factory()
            if args.amazon_user_will_login:
                login_success = nav_to_amazon_and_let_user_login(webdriver)
            else:
                login_success = nav_to_amazon_and_login(
                    webdriver, args.amazon_email, args.amazon_password)
            login_progress.finish()
            if not login_success:
                logger.critical(
                    'Failed to login to Amazon.com')
                return False
            logger.info('Login to Amazon.com successful')

        logger.info('Requesting {} report'.format(report_type))
        request_progress = progress_factory(
            'Requesting {} report '.format(report_shortname), 0)
        request_report(webdriver, report_name, report_type,
                       start_date, end_date)
        request_progress.finish()

    # # Now wait on the reports to be done and then download them.
    # for report_shortname, report_type, report_name, report_path in zip(
    #         report_shortnames, report_types, report_names, report_paths):
    #     if os.path.exists(report_path):
    #         # Report has already been fetched! Woot
    #         continue

        logger.info('Waiting for {} report to be ready'.format(report_type))
        processing_progress = progress_factory(
            'Waiting for {} report to be ready.'.format(
                report_shortname), 0)
        try:
            wait_cond = EC.presence_of_element_located(
                (By.XPATH, get_report_download_link_xpath(report_name)))
            WebDriverWait(webdriver, args.order_history_timeout).until(
                wait_cond)
            processing_progress.finish()
        except TimeoutException:
            processing_progress.finish()
            logger.critical("Cannot find download link after a minute!")
            return False

        logger.info('Downloading {} report'.format(report_type))
        download_progress = progress_factory(
            'Downloading {} report '.format(report_shortname), 0)
        download_report(webdriver, report_name, report_path)
        download_progress.finish()

    args.items_csv = open(report_paths[0], 'r', encoding='utf-8')
    args.orders_csv = open(report_paths[1], 'r', encoding='utf-8')
    args.refunds_csv = open(report_paths[2], 'r', encoding='utf-8')
    return True


def nav_to_amazon_and_let_user_login(webdriver):
    logger.info('User logging in to Amazon.com')

    webdriver.get(ORDER_HISTORY_URL_VIA_SWITCH_ACCOUNT_LOGIN)
    try:
        wait_cond = EC.presence_of_element_located((By.ID, 'report-confirm'))
        WebDriverWait(webdriver, 60 * 5).until(wait_cond)
    except TimeoutException:
        logger.critical('Cannot complete Amazon login!')
        return False
    return True


def nav_to_amazon_and_login(webdriver, email, password):
    logger.info('Starting automated login flow for Amazon.com')

    webdriver.get(ORDER_HISTORY_URL_VIA_SWITCH_ACCOUNT_LOGIN)
    webdriver.implicitly_wait(2)

    # Go straight to the account switcher, and look for the given email.
    # If present, click on it! Otherwise, click on "Add account".
    desired_account_element = get_element_by_xpath(
        webdriver,
        "//div[contains(text(), '{}')]".format(email))
    if desired_account_element:
        desired_account_element.click()
        webdriver.implicitly_wait(3)

        # It's possible this account has already authed recently. If so, the
        # next block will be skipped and the login is complete!
        if not get_element_by_id(webdriver, 'report-confirm'):
            webdriver.find_element_by_id('ap_password').clear()
            webdriver.find_element_by_id('ap_password').send_keys(password)
            webdriver.find_element_by_name('rememberMe').click()
            webdriver.find_element_by_id('signInSubmit').submit()
    else:
        # Cannot find the desired account in the switch. Log in via Add Account
        webdriver.find_element_by_xpath(
            '//div[text()="Add account"]').click()
        webdriver.implicitly_wait(3)

        webdriver.find_element_by_id('ap_email').send_keys(email)

        # Login flow sometimes asks just for the email, then a
        # continue button, then password.
        if get_element_by_id(webdriver, 'continue'):
            webdriver.find_element_by_id('continue').click()
            webdriver.implicitly_wait(3)

        webdriver.find_element_by_id('ap_password').clear()
        webdriver.find_element_by_id('ap_password').send_keys(password)
        webdriver.find_element_by_name('rememberMe').click()
        webdriver.find_element_by_id('signInSubmit').submit()

    webdriver.implicitly_wait(3)

    if not get_element_by_id(webdriver, 'report-confirm'):
        logger.warning('Having trouble logging into Amazon. Please see the '
                       'browser and complete login within the next 5 minutes. '
                       'This script will continue automatically on success. '
                       'You may need to manually navigate to: {}'.format(
                           ORDER_HISTORY_REPORT_URL))
        if get_element_by_id(webdriver, 'auth-mfa-otpcode'):
            logger.warning('Hint: Looks like an auth challenge! Maybe check '
                           'your email')
    try:
        wait_cond = EC.presence_of_element_located((By.ID, 'report-confirm'))
        WebDriverWait(webdriver, 60 * 5).until(wait_cond)
    except TimeoutException:
        logger.critical('Cannot complete Amazon login!')
        return False
    return True


def request_report(webdriver, report_name, report_type, start_date, end_date):
    try:
        # Do not request the report again if it's already available for
        # download.
        webdriver.find_element_by_xpath(
            get_report_download_link_xpath(report_name))
        return
    except NoSuchElementException:
        pass

    Select(webdriver.find_element_by_id(
        'report-type')).select_by_value(report_type)

    Select(webdriver.find_element_by_id(
        'report-month-start')).select_by_value(str(start_date.month))
    Select(webdriver.find_element_by_id(
        'report-day-start')).select_by_value(str(start_date.day))
    Select(webdriver.find_element_by_id(
        'report-year-start')).select_by_value(str(start_date.year))

    Select(webdriver.find_element_by_id(
        'report-month-end')).select_by_value(str(end_date.month))
    Select(webdriver.find_element_by_id(
        'report-day-end')).select_by_value(str(end_date.day))
    Select(webdriver.find_element_by_id(
        'report-year-end')).select_by_value(str(end_date.year))

    webdriver.find_element_by_id('report-name').send_keys(report_name)

    # Submit will not work as the input type is an image (nice Amazon)
    webdriver.find_element_by_id('report-confirm').click()


def get_report_download_link_xpath(report_name):
    return "//td[contains(text(), '{}')]/..//td/a[text()='Download']".format(
        report_name)


def download_report(webdriver, report_name, report_path):
    # 1. Find the report download link
    report_url = None
    try:
        download_link = webdriver.find_element_by_xpath(
            get_report_download_link_xpath(report_name))
        report_url = download_link.get_attribute('href')
    except NoSuchElementException:
        logger.critical('Could not find the download link!')
        exit(1)

    # 2. Download the report to the AMZN Reports directory
    response = webdriver.request('GET', report_url, allow_redirects=True)
    response.raise_for_status()
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write(response.text)
