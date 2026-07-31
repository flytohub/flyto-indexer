"""On-demand dynamic framework relationship tests."""

from src.analyzer.framework_relationships import analyze_framework_relationships


def test_typescript_relationships_cover_lazy_routes_rbac_and_orm(tmp_path):
    source = tmp_path / "routes.tsx"
    source.write_text(
        """
const Admin = React.lazy(() => import('./Admin'))
const pages = import.meta.glob('./pages/**/*.tsx')
router.use('/api/admin', adminRouter)
router.get('/api/admin/users', requirePermission('users.read'), handler)
const users = prisma.user.findMany({ where: { tenantId: tenant.id } })
""",
        encoding="utf-8",
    )

    result = analyze_framework_relationships(str(source))
    kinds = {item["kind"] for item in result["relationships"]}

    assert result["status"] == "analyzed"
    assert kinds == {
        "dynamic_import_glob",
        "orm_tenant_scope",
        "react_lazy_import",
        "route_authorization",
        "route_mount",
    }
    glob = next(
        item for item in result["relationships"] if item["kind"] == "dynamic_import_glob"
    )
    assert glob["metadata"]["resolution"] == "pattern_requires_expansion"
    assert result["performance"] == "on_demand_only"


def test_python_relationships_cover_scope_and_permission(tmp_path):
    source = tmp_path / "views.py"
    source.write_text(
        """
@permission_required('orders.read')
def orders():
    return Order.query.filter_by(tenant_id=current_tenant.id).all()
""",
        encoding="utf-8",
    )

    result = analyze_framework_relationships(str(source))

    assert {item["kind"] for item in result["relationships"]} == {
        "orm_tenant_scope",
        "route_authorization",
    }
