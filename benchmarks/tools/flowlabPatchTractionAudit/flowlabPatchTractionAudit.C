/*---------------------------------------------------------------------------*\
  FlowLab read-only patch traction audit for OpenFOAM 11.

  Reproduces the viscous term used by the OpenFOAM forces function object:

      Sf & [-nu dev(2 symm(grad(U)))]

  and writes the contributing quantities for every selected boundary face.
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
    argList::addOption
    (
        "output",
        "file",
        "CSV destination for the face decomposition"
    );
    argList::addBoolOption
    (
        "allFlowPatches",
        "audit inlet, outlet, yMin, yMax, zMin, and zMax"
    );

    #include "setRootCase.H"
    #include "createTime.H"
    #include "createMesh.H"

    if (!args.optionFound("output"))
    {
        FatalErrorInFunction
            << "The -output <file> option is required"
            << exit(FatalError);
    }

    const instantList selectedTimes = timeSelector::select0(runTime, args);

    if (selectedTimes.size() != 1)
    {
        FatalErrorInFunction
            << "Select exactly one time with -time; selected "
            << selectedTimes.size()
            << exit(FatalError);
    }

    runTime.setTime(selectedTimes[0], 0);
    mesh.readUpdate();

    Info<< "Auditing time " << runTime.name() << nl;

    const volVectorField U
    (
        IOobject
        (
            "U",
            runTime.name(),
            mesh,
            IOobject::MUST_READ,
            IOobject::NO_WRITE
        ),
        mesh
    );

    const volScalarField p
    (
        IOobject
        (
            "p",
            runTime.name(),
            mesh,
            IOobject::MUST_READ,
            IOobject::NO_WRITE
        ),
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

    const dimensionedScalar nu
    (
        "nu",
        dimViscosity,
        physicalProperties.lookup("nu")
    );

    const tmp<volTensorField> tGradU = fvc::grad(U);
    const volTensorField& gradU = tGradU();
    const tmp<volSymmTensorField> tDevTau
    (
        -nu*dev(twoSymm(gradU))
    );
    const volSymmTensorField& devTau = tDevTau();

    const fileName output(args.optionRead<fileName>("output"));
    OFstream csv(output);
    // Seventeen digits preserve a round-trip representation of binary64 data.
    csv.precision(17);
    csv
        << "time,patch,patch_face,mesh_face,owner_cell,"
        << "cf_x,cf_y,cf_z,sf_x,sf_y,sf_z,area,n_x,n_y,n_z,"
        << "boundary_u_x,boundary_u_y,boundary_u_z,"
        << "owner_u_x,owner_u_y,owner_u_z,"
        << "boundary_error_x,boundary_error_y,boundary_error_z,"
        << "owner_error_x,owner_error_y,owner_error_z,"
        << "sn_grad_u_x,sn_grad_u_y,sn_grad_u_z,"
        << "sn_grad_normal_velocity,"
        << "grad_u_xx,grad_u_xy,grad_u_xz,"
        << "grad_u_yx,grad_u_yy,grad_u_yz,"
        << "grad_u_zx,grad_u_zy,grad_u_zz,"
        << "dev_tau_xx,dev_tau_xy,dev_tau_xz,"
        << "dev_tau_yy,dev_tau_yz,dev_tau_zz,"
        << "traction_x,traction_y,traction_z,"
        << "viscous_force_x,viscous_force_y,viscous_force_z,"
        << "pressure,pressure_force_x,pressure_force_y,pressure_force_z"
        << nl;

    wordList patchNames({"inlet", "outlet"});
    if (args.optionFound("allFlowPatches"))
    {
        patchNames = wordList
        ({"inlet", "outlet", "yMin", "yMax", "zMin", "zMax"});
    }
    const vector exactU(1, 0, 0);
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
        const vectorField ownerU(Up.patchInternalField());
        const vectorField snGradU(Up.snGrad());
        const fvPatchScalarField& pp = p.boundaryField()[patchi];
        const tensorField& gradUp = gradU.boundaryField()[patchi];
        const symmTensorField& devTaup = devTau.boundaryField()[patchi];
        const labelUList& ownerCells = patch.faceCells();

        vector patchPressureForce(Zero);
        vector patchViscousForce(Zero);
        scalar patchArea = 0;
        scalar maxMagSnGradU = 0;
        scalar maxAbsSnGradNormalVelocity = 0;

        forAll(patch, facei)
        {
            const scalar area = mag(Sf[facei]);
            const vector normal = Sf[facei]/area;
            const vector traction = normal & devTaup[facei];
            const vector viscousForce = Sf[facei] & devTaup[facei];
            const vector pressureForce = Sf[facei]*pp[facei];
            const scalar snGradNormalVelocity = normal & snGradU[facei];

            patchArea += area;
            patchPressureForce += pressureForce;
            patchViscousForce += viscousForce;
            maxMagSnGradU = max(maxMagSnGradU, mag(snGradU[facei]));
            maxAbsSnGradNormalVelocity = max
            (
                maxAbsSnGradNormalVelocity,
                mag(snGradNormalVelocity)
            );

            const tensor& g = gradUp[facei];
            const symmTensor& tau = devTaup[facei];
            const vector boundaryError = Up[facei] - exactU;
            const vector ownerError = ownerU[facei] - exactU;

            csv
                << runTime.value() << ',' << patchName << ',' << facei << ','
                << patch.start() + facei << ',' << ownerCells[facei] << ','
                << Cf[facei].x() << ',' << Cf[facei].y() << ',' << Cf[facei].z() << ','
                << Sf[facei].x() << ',' << Sf[facei].y() << ',' << Sf[facei].z() << ','
                << area << ',' << normal.x() << ',' << normal.y() << ',' << normal.z() << ','
                << Up[facei].x() << ',' << Up[facei].y() << ',' << Up[facei].z() << ','
                << ownerU[facei].x() << ',' << ownerU[facei].y() << ',' << ownerU[facei].z() << ','
                << boundaryError.x() << ',' << boundaryError.y() << ',' << boundaryError.z() << ','
                << ownerError.x() << ',' << ownerError.y() << ',' << ownerError.z() << ','
                << snGradU[facei].x() << ',' << snGradU[facei].y() << ',' << snGradU[facei].z() << ','
                << snGradNormalVelocity << ','
                << g.xx() << ',' << g.xy() << ',' << g.xz() << ','
                << g.yx() << ',' << g.yy() << ',' << g.yz() << ','
                << g.zx() << ',' << g.zy() << ',' << g.zz() << ','
                << tau.xx() << ',' << tau.xy() << ',' << tau.xz() << ','
                << tau.yy() << ',' << tau.yz() << ',' << tau.zz() << ','
                << traction.x() << ',' << traction.y() << ',' << traction.z() << ','
                << viscousForce.x() << ',' << viscousForce.y() << ',' << viscousForce.z() << ','
                << pp[facei] << ','
                << pressureForce.x() << ',' << pressureForce.y() << ',' << pressureForce.z()
                << nl;
        }

        totalPressureForce += patchPressureForce;
        totalViscousForce += patchViscousForce;

        Info<< "FLOWLAB_PATCH_SUMMARY patch=" << patchName
            << " faces=" << patch.size()
            << " area=" << patchArea
            << " pressureForce=" << patchPressureForce
            << " viscousForce=" << patchViscousForce
            << " maxMagSnGradU=" << maxMagSnGradU
            << " maxAbsSnGradNormalVelocity=" << maxAbsSnGradNormalVelocity
            << nl;
    }

    Info<< "FLOWLAB_TOTAL_SUMMARY pressureForce=" << totalPressureForce
        << " viscousForce=" << totalViscousForce
        << " analyticViscousForce=" << vector::zero
        << nl;
    Info<< "Wrote face decomposition to " << output << nl;
    Info<< "End" << nl;

    return 0;
}
