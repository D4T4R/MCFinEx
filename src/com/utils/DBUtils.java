package com.utils;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

public class DBUtils {
	// JDBC driver name and database URL
	static final String JDBC_DRIVER = "org.h2.Driver";
	static final String DB_URL = "jdbc:h2:tcp://localhost/~/stockDB";

	// Database credentials
	static final String USER = "sa";
	static final String PASS = "sa";

	public static void main(String[] args) {
		Connection conn = null;
		Statement stmt = null;
		try {
			// STEP 1: Register JDBC driver
			// Class.forName(JDBC_DRIVER);

			// STEP 2: Open a connection
			System.out.println("Connecting to database...");
			conn = DriverManager.getConnection(DB_URL, USER, PASS);
			stmt = conn.createStatement();
			String sql = "Select count(*) from stock_master";
			ResultSet rs = stmt.executeQuery(sql);
			if (rs.next()) {
				int iRowCount = rs.getInt(1);
				System.out.println("Total Rows " + iRowCount);
			}

			ArrayList<HashMap<String, String>> alData = new DBUtils().getResults("Select count(*) from stock_master");
			System.out.println(alData.toString());

			// TODO Auto-generated method stub
		} catch (SQLException se) {
			// Handle errors for JDBC
			se.printStackTrace();
		} catch (Exception e) {
			// Handle errors for Class.forName
			e.printStackTrace();
		} finally {
			// finally block used to close resources
			try {
				if (stmt != null)
					stmt.close();
			} catch (SQLException se2) {
			} // nothing we can do
			try {
				if (conn != null)
					conn.close();
			} catch (SQLException se) {
				se.printStackTrace();
			} // end finally try
		} // end try
		System.out.println("Goodbye!");
	}

	private Connection getConnection() {
		try {
			// STEP 1: Register JDBC driver
			// Class.forName(JDBC_DRIVER);

			// STEP 2: Open a connection
			System.out.println("Connecting to database...");
			return DriverManager.getConnection(DB_URL, USER, PASS);
		} catch (SQLException se) {
			// Handle errors for JDBC
			se.printStackTrace();
		} catch (Exception e) {
			// Handle errors for Class.forName
			e.printStackTrace();
		}
		return null;
	}

	public ArrayList<HashMap<String, String>> getResults(String sSQL) {

		Connection conn = null;
		Statement stmt = null;
		ResultSet rs = null;
		ArrayList<HashMap<String, String>> alData = null;

		try {
			conn = getConnection();
			stmt = conn.createStatement();
			rs = stmt.executeQuery(sSQL);
			alData = convertResultSetToHashMap(rs);
		} catch (SQLException sqe) {
			sqe.printStackTrace();
		} finally {
			try {
				rs.close();
			} catch (SQLException sqe) {
				sqe.printStackTrace();
			}
			try {
				stmt.close();
			} catch (SQLException sqe) {
				sqe.printStackTrace();
			}
			try {
				conn.close();
			} catch (SQLException sqe) {
				sqe.printStackTrace();
			}
		}
		return alData;
	}

	public int setData(HashMap<String, String> hmData) {
		Connection conn = null;
		Statement stmt = null;
		String strQuery = null;
		try {
			strQuery = convertMapToSQL(hmData);
			conn = getConnection();
			stmt = conn.createStatement();
			return stmt.executeUpdate(strQuery);
		} catch (SQLException sqe) {
			sqe.printStackTrace();
			return -1;
		} finally {
			try {
				stmt.close();
			} catch (SQLException sqe) {
				sqe.printStackTrace();
			}
			try {
				conn.close();
			} catch (SQLException sqe) {
				sqe.printStackTrace();
			}
		}
	}

	private String convertMapToSQL(HashMap<String, String> hmData) {
		String strQuery = "MERGE into {table} (columns) values (values)";
		String strTableNameReplaced = null;
		String strColumnReplaced = null;
		String strValuesReplaced = null;
		StringBuffer strColumnName = new StringBuffer("(");
		StringBuffer strValue = new StringBuffer("(");
		try {
		for (Map.Entry<String, String> entry : hmData.entrySet()) {

			if ("TABLE".equalsIgnoreCase(entry.getKey())) {
				strTableNameReplaced = strQuery.replace("{table}", entry.getValue());
			} else {
				strColumnName.append(entry.getKey() + ",");
				strValue.append("\'" + entry.getValue() + "\',");
			}
		}
		strColumnName.replace(strColumnName.length() - 1, strColumnName.length(), ")");
		strValue.replace(strValue.length() - 1, strValue.length(), ")");

		strColumnReplaced = strTableNameReplaced.replace("(columns)", strColumnName);
		strValuesReplaced = strColumnReplaced.replace("(values)", strValue);
		} catch (Exception e) {
			e.printStackTrace();
		}
		System.out.println("Query - " + strValuesReplaced);
		return strValuesReplaced;
	}

	/**
	 * Convert REesultSet to Array List of Hashmaps
	 * 
	 * @param rs
	 * @return ArrayList of HashMap
	 */
	private ArrayList<HashMap<String, String>> convertResultSetToHashMap(ResultSet rs) {
		int iColumnCount = 0;
		ArrayList<HashMap<String, String>> al = new ArrayList<HashMap<String, String>>();

		try {
			while (rs.next()) {
				HashMap<String, String> hmResultSet = new HashMap<String, String>();
				iColumnCount = rs.getMetaData().getColumnCount();
				// System.out.println("iColumnCount - " + iColumnCount);
				for (int iColumnCounter = 1; iColumnCounter < iColumnCount + 1; iColumnCounter++) {
					hmResultSet.put(rs.getMetaData().getColumnName(iColumnCounter), rs.getString(iColumnCounter));
				}
				al.add(hmResultSet);
			}

		} catch (SQLException sqe) {
			sqe.printStackTrace();
		}
		return al;
	}
}
