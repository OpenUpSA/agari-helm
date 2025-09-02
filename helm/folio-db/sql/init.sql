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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_T
    IMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE NULL
);

-- Create projects table
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    organization_id VARCHAR(255) NOT NULL, -- Keycloak organization ID
    user_id VARCHAR(255) NOT NULL, -- Keycloak user ID of creator
    pathogen_id UUID REFERENCES pathogens(id),
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'completed', 'archived')),
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
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    start_date DATE,
    end_date DATE,
    status VARCHAR(50) DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'completed', 'archived')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE NULL
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug);
CREATE INDEX IF NOT EXISTS idx_projects_pathogen ON projects(pathogen_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_organization ON projects(organization_id);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_studies_project ON studies(project_id);
CREATE INDEX IF NOT EXISTS idx_studies_study_id ON studies(study_id);
CREATE INDEX IF NOT EXISTS idx_studies_status ON studies(status);
CREATE INDEX IF NOT EXISTS idx_pathogens_name ON pathogens(name);

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

-- No sample data - clean slate for production use

-- Create views for easier querying
CREATE OR REPLACE VIEW project_details AS
SELECT 
    p.id,
    p.slug,
    p.name,
    p.description,
    p.organization_id,
    p.user_id,
    p.status,
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
GROUP BY p.id, p.slug, p.name, p.description, p.organization_id, 
         p.user_id, p.status, p.created_at, p.updated_at, p.deleted_at,
         pat.name, pat.scientific_name;

CREATE OR REPLACE VIEW study_details AS
SELECT 
    s.id,
    s.study_id,
    s.name,
    s.description,
    s.start_date,
    s.end_date,
    s.status,
    s.created_at,
    s.updated_at,
    s.deleted_at,
    p.slug as project_slug,
    p.name as project_name,
    pat.name as pathogen_name
FROM studies s
JOIN projects p ON s.project_id = p.id AND p.deleted_at IS NULL
LEFT JOIN pathogens pat ON p.pathogen_id = pat.id AND pat.deleted_at IS NULL
WHERE s.deleted_at IS NULL;

-- Grant permissions to the folio application user (if needed)
-- Note: This assumes the folio app connects with the same user as the database owner
-- In production, you might want to create a separate application user with limited permissions

COMMENT ON TABLE pathogens IS 'Reference table for pathogen information';
COMMENT ON TABLE projects IS 'Main projects table containing project metadata';
COMMENT ON TABLE studies IS 'Studies table containing study information linked to projects';
COMMENT ON VIEW project_details IS 'Denormalized view of projects with pathogen and study count information';
COMMENT ON VIEW study_details IS 'Denormalized view of studies with project and pathogen information';
