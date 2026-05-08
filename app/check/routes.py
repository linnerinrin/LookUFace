"""
app/check/routes.py
签到签退路由
check_in() 签到
check_out() 签退
put_QRcode 返回支付二维码给前端
"""


from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.auth.routes import get_current_user
from app.check.schemas import CheckOutResponse, CheckInResponse, CheckInRequest, CheckOutRequest, PutQRcodeRequest
from app.config import settings
from app.core.checking import checking
from app.services.checking_service import checking_service
from app.database import User

router = APIRouter(prefix="/check", tags=["计时缴费"])


@router.post("/checkin",response_model=CheckInResponse)
async def check_in(request:CheckInRequest,current_user:User=Depends(get_current_user)):
    """签到"""
    user_id = current_user.id
    name = request.name
    request_times=request.request_times+1
    if request_times<settings.CHECKIN_REQUEST_TIME:
        return CheckInResponse(
            success=False,
            name=name,
            message=None,
            response_times=request_times
        )
    success=checking.check_in(name=name,user_id=user_id)
    return CheckInResponse(
        success=success,
        name=name,
        message=f"欢迎 {name}，离场或暂离请自觉签退付费！",
        response_times=0
        )

@router.post("/checkout",response_model=CheckOutResponse)
async def check_out(request:CheckOutRequest,current_user:User=Depends(get_current_user)):
    """签退"""
    user_id = current_user.id
    name = request.name
    away=request.away
    away_time=request.away_time
    success=checking.check_out(away=away,away_time=away_time,name=name,user_id=user_id)
    if away:
        return CheckOutResponse(
            success=success,
            name=name,
            message=f"用户{name}暂时离场，超过{away_time}时将重新计费！",
            online_time=None
        )
    else:
        return CheckOutResponse(
            success=success,
            name=name,
            message=f"",
            online_time=checking.calculate_online_time(name,user_id)
            )

@router.post("/pay",response_class=FileResponse)
async def put_QRcode(request:PutQRcodeRequest,current_user:User=Depends(get_current_user)):
    """绘制支付二维码到前端"""
    user_id = current_user.id
    name = request.name
    online_time=request.online_time
    QRcode_path=checking_service.drawing_costQRcode(name=name,online_time=online_time)
    return FileResponse(str(QRcode_path))