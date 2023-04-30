package com.utils;

import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;


import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.Keys;
import org.openqa.selenium.WebDriver;

import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.edge.EdgeDriver;
import org.openqa.selenium.edge.EdgeOptions;
import org.openqa.selenium.interactions.Actions;

import org.openqa.selenium.support.ui.WebDriverWait;

import java.time.Duration;

public class SeleniumSupport {
	
	public WebDriver drv;
	public JavascriptExecutor executor;
	public Actions action;
	public WebDriverWait wait;
	private static Duration timeout = Duration.ofSeconds(10);

	public void initBrowser(String sBrowser, String sUrl) {

		boolean readyStateComplete = false;

		if ("edge".equals(sBrowser)) {
			System.setProperty("webdriver.edge.driver", "E:\\Selenium\\msedgedriver.exe");
			EdgeOptions options = new EdgeOptions();
			System.out.println("options init " + options.getCapability("headless"));
			options.setCapability("headless", true);
			// options.setCapability("disable-gpu",true);
			System.out.println("options " + options.getCapability("headless"));

			drv = new EdgeDriver(options);
		} else if ("chrome".equals(sBrowser)) {
			System.setProperty("webdriver.chrome.driver", "E:\\Selenium\\chromedriver.exe");
			ChromeOptions options = new ChromeOptions();
			options.addArguments("--headless", "--disable-gpu", "--window-size=1600,900", "--ignore-certificate-errors",
					"--disable-extensions", "--no-sandbox", "--disable-dev-shm-usage"); //
			drv = new ChromeDriver(options);
		} else {
			System.out.println("Please suggest browser");
		}

		executor = (JavascriptExecutor) drv;
		wait = new WebDriverWait(drv, timeout);
		drv.manage().window().maximize();
		drv.get(sUrl);

		makeMeWait(1000);
		while (!readyStateComplete)
			readyStateComplete = ((String) executor.executeScript("return document.readyState")).equals("complete");

	}

	public void shutdownChrome() {
		drv.close();
		drv.quit();
	}
	
	public WebElement findElementByXPath(String strXPath) {

		int iCount = 0;
		WebElement webelmt = null;
		do {
			try {
				webelmt = drv.findElement(By.xpath(strXPath));
			} catch (Exception e) {
				iCount++;
				makeMeWait(2000);
			}
		} while (null == webelmt && iCount < 5);
		return webelmt;

	}

	public void clickOnElementbyXpath(String strElementCommonName, String strXPath, String... sValue) {
		try {

			WebElement webelmt = findElementByXPath(strXPath);
			action.moveToElement(webelmt);
			System.out.println("webelmt.getText() " + webelmt.getText());

			// wait.until(ExpectedConditions.elementToBeClickable(webelmt));
			if (null != sValue && 0 < sValue.length) {
				if (1 > webelmt.getText().length()) {

				}
				if (webelmt.getText().contains(sValue[0])) {
					webelmt.click();
					return;
				} else
					return;
			}
			webelmt.click();

		} catch (Exception e) {
			System.out.println("Could not action Element  " + strElementCommonName);
			e.printStackTrace();
		}
	}

	public void clickOnElementbyXpathSuggestionBox(String strElementCommonName, String strXPath,
			String... sValue) {
		try {

			WebElement webelmt = findElementByXPath(strXPath);
			action.moveToElement(webelmt);
			// System.out.println("webelmt.getText() " + webelmt.getText());

			// wait.until(ExpectedConditions.elementToBeClickable(webelmt));
			if (null != sValue && 0 < sValue.length) {
				if (1 > webelmt.getText().length()) {

				}
				if (webelmt.getText().contains(sValue[0])) {
					webelmt.click();
					return;
				} else
					return;
			}

		} catch (Exception e) {
			System.out.println("Could not action Element  " + strElementCommonName);
			e.printStackTrace();
		}
	}
	
	public String getElementValuebyXpath(String strElementCommonName, String strXPath) {

		WebElement webelmt = findElementByXPath(strXPath);

		try {

			if (null != webelmt) {
				// System.out.println(strElementCommonName + " webelmt.isDisplayed() " +
				// webelmt.isDisplayed() );
				action.moveToElement(webelmt);
				wait.until(ExpectedConditions.visibilityOf(webelmt));
				action.moveToElement(webelmt);
				// System.out.println(webelmt.getText());
				if (webelmt.getText().strip().length() > 0)
					return webelmt.getText().replaceAll(",", "").replaceAll("--", "-5555555");
				else
					return "-8888888";
			} else
				return "-777777";
		} catch (Exception e) {
			e.printStackTrace();
			System.out.println("----------Could not action Element  " + strElementCommonName);
			// e.printStackTrace();
			return "-9999999";
		}
	}

	public void setElementValuebyXpath(String strElementCommonName, String strXPath, String strValue) {
		try {
			WebElement webelmt = findElementByXPath(strXPath);
			action.moveToElement(webelmt);
			wait.until(ExpectedConditions.visibilityOf(webelmt));
			webelmt.clear();
			webelmt.sendKeys(strValue);
			webelmt.sendKeys(" ");
		} catch (Exception e) {
			System.out.println("Could not action Element  " + strElementCommonName);
			e.printStackTrace();
		}
	}

	public void makeMeWait(long timeInMillis) {
		try {
			Thread.sleep(timeInMillis);
			// drv.manage().timeouts().implicitlyWait(timeInMillis, TimeUnit.MILLISECONDS);
		} catch (Exception e) {
		}
	}
	
	public void refreshPage() {
		action = new Actions(drv);
		action.sendKeys(Keys.HOME).build().perform();
	}

	
	public void changeBrowserTab() {

		boolean readyStateComplete = false;

		for (String handle : drv.getWindowHandles()) {
			drv.switchTo().window(handle);
		}

		while (!readyStateComplete)
			readyStateComplete = ((String) executor.executeScript("return document.readyState")).equals("complete");
	}

}
