DROP TABLE IF EXISTS hospitals;
DROP TABLE IF EXISTS organ_listings;
DROP TABLE IF EXISTS transport_requests;
DROP TABLE IF EXISTS drivers;
DROP TABLE IF EXISTS driver_applications;

CREATE TABLE hospitals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  city TEXT,
  state TEXT,
  email TEXT
);

CREATE TABLE organ_listings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hospital_id INTEGER,
  hospital_name TEXT,
  organ_type TEXT,
  blood_type TEXT,
  age INTEGER,
  weight_kg REAL,
  priority_status TEXT DEFAULT 'Normal',
  availability_status TEXT DEFAULT 'Available',
  city TEXT,
  state TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Unified order table visible to hospitals & drivers
CREATE TABLE transport_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_id INTEGER,
  hospital TEXT,              -- requesting hospital name (must be from hospitals table)
  organ_type TEXT,
  origin TEXT,                -- pickup location derived from organ listing hospital
  destination TEXT,           -- delivery address / hospital
  contact_phone TEXT,
  notes TEXT,
  priority_status TEXT,       -- Normal / Critical / Urgent / Emergency
  status TEXT DEFAULT 'Requested',  -- Requested, Assigned, En-route, Delivered
  driver_id INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE drivers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  phone TEXT,
  cdl TEXT
);

CREATE TABLE driver_applications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  phone TEXT,
  cdl TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Seed hospitals
INSERT INTO hospitals(name,city,state,email) VALUES
('Johns Hopkins Hospital','Baltimore','MD','contact@jhh.org'),
('Mayo Clinic','Rochester','MN','organs@mayo.org'),
('Cleveland Clinic','Cleveland','OH','cc@cleveland.org'),
('NYU Langone','New York','NY','nyu@langone.org'),
('Chicago Medical Center','Chicago','IL','chicagomedicalcenter@cmc.org');

-- Seed organ listings
INSERT INTO organ_listings(hospital_id,hospital_name,organ_type,blood_type,age,weight_kg,priority_status,availability_status,city,state) VALUES
(1,'Johns Hopkins Hospital','Heart','O+',22,78.5,'Critical','Available','Baltimore','MD'),
(2,'Mayo Clinic','Kidney','A-',35,64.2,'Normal','Available','Rochester','MN'),
(3,'Cleveland Clinic','Liver','B+',41,70.1,'Urgent','Available','Cleveland','OH'),
(4,'NYU Langone','Cornea','AB+',28,70.0,'Normal','Available','New York','NY'),
(5,'Chicago Medical Center','Cornea','AB+',41,67.0,'Normal','Available','Chicago','IL');

-- Seed a couple of demo transport requests
INSERT INTO transport_requests(listing_id,hospital,organ_type,origin,destination,contact_phone,notes,priority_status,status,driver_id)
VALUES
(1,'Mass General Hospital','Heart','Johns Hopkins Hospital (Baltimore, MD)','Mass General Hospital (Boston, MA)','+1-617-555-0101','High-priority heart transport.','Critical','Requested',NULL),
(2,'UChicago Medicine','Kidney','Mayo Clinic (Rochester, MN)','UChicago Medicine (Chicago, IL)','+1-773-555-0202','Standard kidney transport.','Normal','Requested',NULL),
(3,'Chicago Medical Center','Cornea','Chicago Medical Center (Chicago, IL)','UChicago Medicine (Chicago, IL)','+1-773-555-0202','Standard cornea transport.','Normal','Requested',NULL);
