/*---------------------------------------------------------------------------*\
  Read-only direct face integration for the FDA nozzle campaign.

  OpenFOAM stores incompressible pressure and viscous stress per unit density.
  This utility multiplies both by the frozen physical density so its forces are
  directly comparable with the OpenFOAM forces function object configured with
  rhoInf.
\*---------------------------------------------------------------------------*/

#include "argList.H"
#include "timeSelector.H"
#include "fvMesh.H"
#include "volFields.H"
#include "surfaceFields.H"
#include "fvcGrad.H"
#include "OFstream.H"

using namespace Foam;


int main(int argc, char *argv[])
{
    timeSelector::addOptions();
    argList::addOption("output", "file", "CSV destination");
    argList::addOption("rho", "scalar", "physical density in kg/m3");

    #include "setRootCase.H"
    #include "createTime.H"
    #include "createMesh.H"

    if (!args.optionFound("output") || !args.optionFound("rho"))
    {
        FatalErrorInFunction
            << "Both -output <file> and -rho <scalar> are required"
            << exit(FatalError);
    }

    const instantList selectedTimes = timeSelector::select0(runTime, args);
    if (selectedTimes.size() != 1)
    {
        FatalErrorInFunction
            << "Select exactly one time with -time"
            << exit(FatalError);
    }
    runTime.setTime(selectedTimes[0], 0);
    mesh.readUpdate();

    const scalar rho = args.optionRead<scalar>("rho");
    const volVectorField U
    (
        IOobject("U", runTime.name(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE),
        mesh
    );
    const volScalarField p
    (
        IOobject("p", runTime.name(), mesh, IOobject::MUST_READ, IOobject::NO_WRITE),
        mesh
    );
    const IOdictionary physicalProperties
    (
        IOobject
        (
            "physicalProperties",
            runTime.constant(),
            mesh,
            IOobject::MUST_READ,
            IOobject::NO_WRITE
        )
    );
    const dimensionedScalar nu("nu", dimViscosity, physicalProperties.lookup("nu"));
    const tmp<volTensorField> tGradU = fvc::grad(U);
    const volTensorField& gradU = tGradU();
    const tmp<volSymmTensorField> tDevTau(-nu*dev(twoSymm(gradU)));
    const volSymmTensorField& devTau = tDevTau();

    OFstream csv(args.optionRead<fileName>("output"));
    csv.precision(17);
    csv
        << "time,patch,patch_face,mesh_face,owner_cell,"
        << "cf_x,cf_y,cf_z,sf_x,sf_y,sf_z,area,n_x,n_y,n_z,"
        << "boundary_u_x,boundary_u_y,boundary_u_z,"
        << "sn_grad_u_x,sn_grad_u_y,sn_grad_u_z,sn_grad_normal_velocity,"
        << "grad_u_xx,grad_u_xy,grad_u_xz,grad_u_yx,grad_u_yy,grad_u_yz,"
        << "grad_u_zx,grad_u_zy,grad_u_zz,pressure_pa,"
        << "pressure_force_x_n,pressure_force_y_n,pressure_force_z_n,"
        << "traction_x_pa,traction_y_pa,traction_z_pa,"
        << "viscous_force_x_n,viscous_force_y_n,viscous_force_z_n"
        << nl;

    const wordList patchNames({"inlet", "outlet", "wall"});
    vector totalPressureForce(Zero);
    vector totalViscousForce(Zero);
    forAll(patchNames, patchNameI)
    {
        const word& patchName = patchNames[patchNameI];
        const label patchi = mesh.boundaryMesh().findPatchID(patchName);
        if (patchi < 0)
        {
            FatalErrorInFunction
                << "Required patch '" << patchName << "' was not found"
                << exit(FatalError);
        }
        const fvPatch& patch = mesh.boundary()[patchi];
        const vectorField& Sf = mesh.Sf().boundaryField()[patchi];
        const vectorField& Cf = mesh.Cf().boundaryField()[patchi];
        const fvPatchVectorField& Up = U.boundaryField()[patchi];
        const vectorField snGradU(Up.snGrad());
        const fvPatchScalarField& pp = p.boundaryField()[patchi];
        const tensorField& gradUp = gradU.boundaryField()[patchi];
        const symmTensorField& devTaup = devTau.boundaryField()[patchi];
        const labelUList& ownerCells = patch.faceCells();
        vector patchPressureForce(Zero);
        vector patchViscousForce(Zero);

        forAll(patch, facei)
        {
            const scalar area = mag(Sf[facei]);
            const vector normal = Sf[facei]/area;
            const vector traction = rho*(normal & devTaup[facei]);
            const vector viscousForce = rho*(Sf[facei] & devTaup[facei]);
            const scalar pressurePa = rho*pp[facei];
            const vector pressureForce = Sf[facei]*pressurePa;
            const tensor& g = gradUp[facei];
            patchPressureForce += pressureForce;
            patchViscousForce += viscousForce;

            csv
                << runTime.value() << ',' << patchName << ',' << facei << ','
                << patch.start() + facei << ',' << ownerCells[facei] << ','
                << Cf[facei].x() << ',' << Cf[facei].y() << ',' << Cf[facei].z() << ','
                << Sf[facei].x() << ',' << Sf[facei].y() << ',' << Sf[facei].z() << ','
                << area << ',' << normal.x() << ',' << normal.y() << ',' << normal.z() << ','
                << Up[facei].x() << ',' << Up[facei].y() << ',' << Up[facei].z() << ','
                << snGradU[facei].x() << ',' << snGradU[facei].y() << ',' << snGradU[facei].z() << ','
                << (normal & snGradU[facei]) << ','
                << g.xx() << ',' << g.xy() << ',' << g.xz() << ','
                << g.yx() << ',' << g.yy() << ',' << g.yz() << ','
                << g.zx() << ',' << g.zy() << ',' << g.zz() << ','
                << pressurePa << ','
                << pressureForce.x() << ',' << pressureForce.y() << ',' << pressureForce.z() << ','
                << traction.x() << ',' << traction.y() << ',' << traction.z() << ','
                << viscousForce.x() << ',' << viscousForce.y() << ',' << viscousForce.z()
                << nl;
        }
        totalPressureForce += patchPressureForce;
        totalViscousForce += patchViscousForce;
        Info<< "FLOWLAB_FDA_PATCH patch=" << patchName
            << " pressureForce=" << patchPressureForce
            << " viscousForce=" << patchViscousForce << nl;
    }
    Info<< "FLOWLAB_FDA_TOTAL pressureForce=" << totalPressureForce
        << " viscousForce=" << totalViscousForce << nl;
    Info<< "Wrote " << args.optionRead<fileName>("output") << nl;
    Info<< "End" << nl;
    return 0;
}
