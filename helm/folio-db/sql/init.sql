-- Folio Database Initialization Script
-- Creates tables for projects, pathogens, and studies

-- Enable UUID extension for generating UUIDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create pathogens table
CREATE TABLE IF NOT EXISTS pathogens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    scientific_name VARCHAR(255),
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE NULL
);

-- Create projects table
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    organisation_id VARCHAR(255) NOT NULL DEFAULT 'default-org', -- Keycloak organisation ID
    user_id VARCHAR(255) NOT NULL, -- Keycloak user ID of creator
    pathogen_id UUID REFERENCES pathogens(id),
    privacy VARCHAR(20) DEFAULT 'public' CHECK (privacy IN ('public', 'private')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE NULL
);

-- Create organisations table
CREATE TABLE IF NOT EXISTS organisations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    abbreviation VARCHAR(50),
    url VARCHAR(255),
    about TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE NULL
);

-- Create studies table
CREATE TABLE IF NOT EXISTS studies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    study_id VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    privacy VARCHAR(20) DEFAULT 'public' CHECK (privacy IN ('public', 'private')),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE NULL
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_projects_pathogen ON projects(pathogen_id);
CREATE INDEX IF NOT EXISTS idx_projects_organisation ON projects(organisation_id);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_privacy ON projects(privacy);
CREATE INDEX IF NOT EXISTS idx_studies_project ON studies(project_id);
CREATE INDEX IF NOT EXISTS idx_studies_study_id ON studies(study_id);
CREATE INDEX IF NOT EXISTS idx_pathogens_name ON pathogens(name);
CREATE INDEX IF NOT EXISTS idx_organisations_name ON organisations(name);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers to automatically update updated_at columns
CREATE TRIGGER update_pathogens_updated_at 
    BEFORE UPDATE ON pathogens 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_projects_updated_at 
    BEFORE UPDATE ON projects 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_studies_updated_at 
    BEFORE UPDATE ON studies 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_organisations_updated_at 
    BEFORE UPDATE ON organisations 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Create views for easier querying
CREATE OR REPLACE VIEW project_details AS
SELECT 
    p.id,
    p.name,
    p.description,
    p.organisation_id,
    p.user_id,
    p.privacy,
    p.created_at,
    p.updated_at,
    p.deleted_at,
    pat.name as pathogen_name,
    pat.scientific_name as pathogen_scientific_name,
    COUNT(s.id) as study_count
FROM projects p
LEFT JOIN pathogens pat ON p.pathogen_id = pat.id AND pat.deleted_at IS NULL
LEFT JOIN studies s ON p.id = s.project_id AND s.deleted_at IS NULL
WHERE p.deleted_at IS NULL
GROUP BY p.id, p.name, p.description, p.organisation_id, 
         p.user_id, p.privacy, p.created_at, p.updated_at, p.deleted_at,
         pat.name, pat.scientific_name;

CREATE OR REPLACE VIEW study_details AS
SELECT 
    s.id,
    s.study_id,
    s.name,
    s.description,
    s.status,
    s.privacy,
    s.project_id,
    s.created_at,
    s.updated_at,
    s.deleted_at,
    p.name as project_name,
    pat.name as pathogen_name
FROM studies s
JOIN projects p ON s.project_id = p.id AND p.deleted_at IS NULL
LEFT JOIN pathogens pat ON p.pathogen_id = pat.id AND pat.deleted_at IS NULL
WHERE s.deleted_at IS NULL;

CREATE OR REPLACE VIEW organisation_projects AS
SELECT 
    o.id as organisation_id,
    o.name as organisation_name,
    COUNT(p.id) as project_count
FROM organisations o
LEFT JOIN projects p ON o.id = p.organisation_id AND p.deleted_at IS NULL
WHERE o.deleted_at IS NULL
GROUP BY o.id, o.name;  

COMMENT ON TABLE pathogens IS 'Reference table for pathogen information';
COMMENT ON TABLE projects IS 'Main projects table containing project metadata';
COMMENT ON TABLE studies IS 'Studies table containing study information linked to projects';
COMMENT ON VIEW project_details IS 'Denormalized view of projects with pathogen and study count information';
COMMENT ON VIEW study_details IS 'Denormalized view of studies with project and pathogen information';
COMMENT ON TABLE organisations IS 'Table containing organisation information';