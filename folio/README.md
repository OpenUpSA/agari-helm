What folio should do:

Folio manages projects, pathogens and studies.

The super admin user can create projects and pathogens using:

client: dms 
resource: folio
scopes: READ, WRITE

pathogens should have a uuid, name, slug and description.
projects should have a uuid, name, slug and description.

Once a project is created, the admin user can create studies under that project and assign pathogens to the project.

It will then add the project as a resource to the dms client in keycloak with READ and WRITE scopes - ie. folio.<project_slug>. It will then create an admin group for that project - ie. folio-<project_slug>-admin and assign the user to that group. A group policy will be created for that group and assigned to the folio.<project_slug> resource with READ and WRITE scopes.